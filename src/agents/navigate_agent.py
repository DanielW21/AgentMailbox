import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict

src_path = str(Path(__file__).resolve().parents[1])
if src_path not in sys.path:
    sys.path.insert(0, src_path)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
BROWSERUSE_DIR = PROJECT_ROOT / ".browseruse"


config_dir = os.getenv("BROWSERUSE_CONFIG_DIR", str(BROWSERUSE_DIR))
Path(config_dir).mkdir(parents=True, exist_ok=True)
os.environ["BROWSER_USE_CONFIG_DIR"] = config_dir

from browser_use import Agent, Browser, ChatGoogle
from dotenv import load_dotenv
import google.genai as genai

load_dotenv()


class NavigateAgent:
    BASE_URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
    VALID_DOCUMENT_TYPES = {"Exhibits", "Key Documents", "Other Documents", "Transcripts", "Recordings"}
    MATTER_NUMBER_PATTERN = r"M\d{5}"

    def __init__(self, model: str = "gemini-2.5-pro", headless: bool = True, download_root: str = None):
        """Initialize navigate agent with Gemini model and browser config."""
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("Missing GEMINI_API_KEY or GOOGLE_API_KEY in environment.")

        self.model = model
        self.headless = headless
        self.download_root = Path(download_root).resolve() if download_root else DOWNLOADS_DIR
        self.download_root.mkdir(parents=True, exist_ok=True)
        self.client = genai.Client(api_key=api_key)
        self.llm = ChatGoogle(
            model=self.model,
            api_key=api_key,
            temperature=0,
        )

    def run_navigation(self, matter_number: str, document_type: str, max_documents: int = 10) -> Dict:
        """Execute the browser workflow and return structured results."""
        validation_error = self._validate_inputs(matter_number, document_type, max_documents)
        if validation_error:
            return self._failure_response(matter_number, document_type, validation_error)

        try:
            asyncio.get_running_loop()
            return self._failure_response(
                matter_number,
                document_type,
                "run_navigation cannot be called from an active event loop. Use arun_navigation instead.",
            )
        except RuntimeError:
            return asyncio.run(self.arun_navigation(matter_number, document_type, max_documents))

    async def arun_navigation(self, matter_number: str, document_type: str, max_documents: int = 10) -> Dict:
        """Execute the browser workflow in async contexts."""
        validation_error = self._validate_inputs(matter_number, document_type, max_documents)
        if validation_error:
            return self._failure_response(matter_number, document_type, validation_error)

        browser = None
        matter_download_path = self.download_root / matter_number
        matter_download_path.mkdir(parents=True, exist_ok=True)
        try:
            task = self._build_navigation_task(matter_number, document_type, max_documents)
            browser = Browser(headless=self.headless, downloads_path=str(matter_download_path))
            agent = Agent(task=task, llm=self.llm, browser=browser)
            history = await agent.run()
            raw_result = history.final_result() if history else ""

            summary = self._summarize_result(raw_result, matter_number, document_type, max_documents)

            return {
                "success": True,
                "matter_number": matter_number,
                "document_type": document_type,
                "model": self.model,
                "max_documents_requested": max_documents,
                "navigation_result": raw_result,
                "summary": summary,
                "error_message": None,
            }
            
        except Exception as e:
            return self._failure_response(matter_number, document_type, f"Navigation failed: {str(e)}")
        finally:
            if browser:
                stop_method = getattr(browser, "stop", None)
                if callable(stop_method):
                    await stop_method()

    def _validate_inputs(self, matter_number: str, document_type: str, max_documents: int) -> str:
        """Validate matter number, document type, and document limit."""
        if not isinstance(matter_number, str) or not re.fullmatch(self.MATTER_NUMBER_PATTERN, matter_number):
            return "Matter number must match format M##### (for example M12205)."

        if document_type not in self.VALID_DOCUMENT_TYPES:
            valid_types = ", ".join(sorted(self.VALID_DOCUMENT_TYPES))
            return f"Document type must be one of: {valid_types}."

        if not isinstance(max_documents, int) or max_documents <= 0:
            return "max_documents must be a positive integer."

        return ""

    def _build_navigation_task(self, matter_number: str, document_type: str, max_documents: int) -> str:
        """Build deterministic browsing instructions for browser-use agent."""
        return (
            "Execute this workflow; retries and interim failures are acceptable. "
            f"1) Navigate to {self.BASE_URL}. "
            "2) Find the 'Go Directly to Matter' field and click it to focus. "
            f"3) Type the matter number '{matter_number}' and wait 1 second for the text to register. "
            "4) Read the field value and verify it is populated with text before continuing. "
            f"5) Read the field value and confirm it is exactly '{matter_number}'. If not, go back to step 2 and retry (max 3 times). "
            f"6) ONLY AFTER step 5 succeeds, click the Search button and wait 3 seconds. "
            f"7) Verify page shows matter '{matter_number}' (e.g. Matter No / Matter Number). If wrong matter appears, go back to step 2 and retry once more. "
            "8) Only after successful verification, extract metadata: Matter Number, Title, Description, Type, Category, Date Received, Decision Date. "
            "9) Extract document counts from ALL tabs: count documents in Exhibits, Key Documents, Other Documents, Transcripts, Recordings (even if 0). "
            f"10) Open '{document_type}' tab and download up to {max_documents} files using 'Go Get It'. "
            "11) Final output MUST be plain text with these exact sections: "
            "METADATA, DOCUMENT_COUNTS, DOWNLOADED_FILES, FINAL_STATUS. "
            f"Set FINAL_STATUS to SUCCESS only if verified matter is exactly '{matter_number}' and DOWNLOADED_FILES contains the files actually downloaded in this run (up to {max_documents}). "
            "Final success is based on end result: correct matter and correct downloaded-files list; transient retries/failures do not fail the task if final output meets this."
        )


    def _summarize_result(self, raw_result: str, matter_number: str, document_type: str, max_documents: int) -> Dict:
        """Convert raw browser trace output into a structured summary dict."""
        prompt = f"""
            You are a strict JSON formatter. Summarize this browser automation output into valid JSON.

            Return ONLY the JSON object (no markdown, no code blocks). Include these keys:
            - matter_number
            - document_type
            - downloaded_count (integer)
            - downloaded_files (array of filenames)
            - matter_summary
            - notes

            Context:
            - matter_number: {matter_number}
            - document_type: {document_type}
            - max_documents_requested: {max_documents}

            Browser output:
            {raw_result}
            """
        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = response.text or "{}"
            
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {
                    "status": False,
                    "notes": f"Error parsing summary JSON"
                }
        except Exception:
            return {
                "status": False,
                "notes": f"Error generating summary"
            }

    def _failure_response(self, matter_number: str, document_type: str, error_message: str) -> Dict:
        """Create a standardized failure payload."""
        return {
            "success": False,
            "matter_number": matter_number if isinstance(matter_number, str) else None,
            "document_type": document_type if isinstance(document_type, str) else None,
            "model": self.model,
            "max_documents_requested": None,
            "navigation_result": None,
            "summary": None,
            "error_message": error_message,
        }


if __name__ == "__main__":
    agent = NavigateAgent(model="gemini-2.5-pro", headless=True)
    result = agent.run_navigation(
        matter_number="M12116",
        document_type="Other Documents",
        max_documents=5,
    )
    
    print(json.dumps(result, indent=2))