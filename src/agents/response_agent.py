import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
import google.genai as genai

load_dotenv()


class ResponseAgent:
    """Generate the outbound email body from navigate agent JSON."""

    def __init__(self, model: str = "gemini-2.5-flash"):
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("Missing GEMINI_API_KEY or GOOGLE_API_KEY in environment.")

        self.client = genai.Client(api_key=api_key)
        self.model = model

    def build_response(
        self,
        email_text: str,
        navigate_result: Dict[str, Any],
        zip_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(navigate_result, dict):
            return self._failure_response("Invalid navigate_result payload.")

        if not navigate_result.get("success"):
            return self._failure_response(navigate_result.get("error_message") or "Navigation failed.")

        matter_number = self._safe_str(navigate_result.get("matter_number")) or "Unknown Matter"
        document_type = self._safe_str(navigate_result.get("document_type")) or "documents"

        prompt = self._build_prompt(email_text, navigate_result)

        try:
            response = self.client.models.generate_content(model=self.model, contents=prompt)
            parsed = self._parse_json_response(response.text or "")
            if not parsed:
                return self._failure_response("Could not parse response summary from model.")

            subject = self._safe_str(parsed.get("subject")) or f"{matter_number} {document_type} package"
            body = self._safe_str(parsed.get("body"))
            if not body:
                return self._failure_response("Model response did not include a body.")

            return {
                "success": True,
                "subject": subject,
                "body": body,
                "attachment_path": zip_path,
                "matter_number": matter_number,
                "document_type": document_type,
                "error_message": None,
            }
        except Exception as exc:
            return self._failure_response(f"Response generation failed: {str(exc)}")

    def _build_prompt(self, email_text: str, navigate_result: Dict[str, Any]) -> str:
        navigate_json = json.dumps(navigate_result, ensure_ascii=True, indent=2)
        return f"""
            You are writing the final outbound email for a regulatory document request.

            Input data is the full navigate agent JSON below. Use only that data.
            If any field is missing, write "Unknown" instead of inventing details.

            Original inbound email text:
            {email_text}

            Output requirements:
            - Return ONLY valid JSON with keys: subject, body
            - body must be one concise paragraph in this exact style guideline:
            "Hi User, M12205 is about the Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,270,000. It relates to Capital Expenditure within the Water category. The matter had an initial filing on April 7, 2025 and a final filing on October 23, 2025. I found 13 Exhibits, 5 Key Documents, 21 Other Documents, and no Transcripts or Recordings. I downloaded 10 out of the 21 Other Documents and am attaching them as a ZIP here."
            - Extract recipient name from the inbound email greeting if clearly present.
            - If recipient name is missing or unclear, use "User" (e.g., "Hi User,")
            - Keep the wording natural and close to the style above.
            - End with mention of ZIP attachment of results.

            Navigate agent JSON:
            {navigate_json}
            """

    def _parse_json_response(self, text: str) -> Optional[Dict[str, Any]]:
        cleaned = self._safe_str(text)
        if not cleaned:
            return None

        if "```json" in cleaned:
            cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    def _safe_str(self, value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    def _failure_response(self, error_message: str) -> Dict[str, Any]:
        return {
            "success": False,
            "subject": None,
            "body": None,
            "attachment_path": None,
            "matter_number": None,
            "document_type": None,
            "error_message": error_message,
        }


if __name__ == "__main__":
    sample_email = "Hi, can you send me Other Documents from M12205? Thanks!"
    sample_navigation = {
        "success": True,
        "matter_number": "M12205",
        "document_type": "Other Documents",
        "model": "gemini-2.5-pro",
        "max_documents_requested": 1,
        "navigation_result": "METADATA:\nMatter Number: M12205\nTitle: Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000\nDescription: Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000\nType: Water\nCategory: Capital Expenditure Approvals\nDate Received: 04/07/2025\nDecision Date: 10/23/2025\n\nDOCUMENT_COUNTS:\nExhibits: 13\nKey Documents: 5\nOther Documents: 21\nTranscripts: 0\nRecordings: 0\n\nDOWNLOADED_FILES:\n100243.pdf\n\nFINAL_STATUS:\nSUCCESS",
        "summary": {
            "matter_number": "M12205",
            "document_type": "Other Documents",
            "downloaded_count": 1,
            "downloaded_files": ["100243.pdf"],
            "matter_summary": "Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,275,000",
            "notes": "Matter verified and files downloaded.",
        },
        "error_message": None,
    }

    result = ResponseAgent().build_response(sample_email, sample_navigation)
    print(json.dumps(result, indent=2))
