import os
import re
import json
from typing import Dict
from dotenv import load_dotenv
import google.genai as genai

load_dotenv()


class IntakeAgent:
    VALID_DOCUMENT_TYPES = { "Exhibits", "Key Documents", "Other Documents", "Transcripts", "Recordings"}
    MATTER_NUMBER_PATTERN = r"M\d{5}"

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-2.5-flash-lite"

    def parse_email(self, email_text: str) -> Dict:
        """Parse email text to extract a single matter number and document type."""

        if not email_text or not isinstance(email_text, str):
            return self._failure_response("Email text must be a non-empty string.")

        try:
            prompt = self._build_extraction_prompt(email_text)

            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt
            )
            gemini_response = response.text

            result = self._parse_gemini_response(gemini_response)

            return result

        except Exception as e:
            return self._failure_response(
                f"Error processing email with Gemini API: {str(e)}"
            )

    def _build_extraction_prompt(self, email_text: str) -> str:
        valid_types = ", ".join(self.VALID_DOCUMENT_TYPES)

        prompt = f"""You are an expert at STRICTLY extracting structured information from emails. You must be VERY STRICT and NOT make any assumptions or guesses.

            Your task is to analyze the following email and extract:
            1. EXACTLY ONE matter number (format: M followed by 5 digits, e.g., M12205)
            2. EXACTLY ONE document type that is EXPLICITLY mentioned and matches one from this exact list: {valid_types}

            Email text:
            ---
            {email_text}
            ---

            Respond ONLY with a JSON object in this exact format:
            {{
                "success": true,
                "matter_number": "M12205",
                "document_type": "Other Documents",
                "error_message": null
            }}

            OR if you cannot extract exactly one valid matter number and one valid document type:
            {{
                "success": false,
                "matter_number": null,
                "document_type": null,
                "error_message": "A specific message explaining what information is missing or invalid"
            }}

            STRICT Rules to follow EXACTLY:
            - ONLY set success to true if BOTH a valid matter number AND a valid document type are EXPLICITLY found
            - If the email mentions multiple matter numbers OR multiple document types, set success to false
            - Document type MUST be explicitly mentioned in the email AND match EXACTLY one of: {valid_types}
            - If a document type is PARTIALLY mentioned but not exact match (e.g., "PDFs", "files", "documents"), set success to false
            - If the document type is missing, ambiguous, or not in the valid list, set success to false
            - Only return a matter number that matches the M##### format (5 digits)
            - DO NOT GUESS or assume missing information - if something is unclear or missing, set success to false
            """

        return prompt

    def _parse_gemini_response(self, response_text: str) -> Dict:
        """Parse the JSON response from Gemini."""
        try:
            response_text = response_text.strip()
            
            if "```" in response_text:
                start = response_text.find("```")
                end = response_text.rfind("```")
                if start != -1 and end != -1 and start < end:
                    response_text = response_text[start+3:end].strip()
                    if response_text.startswith("json"):
                        response_text = response_text[4:].strip()

            parsed = json.loads(response_text)

            success = parsed.get("success", False)
            matter_number = parsed.get("matter_number")
            document_type = parsed.get("document_type")
            error_message = parsed.get("error_message")

            # Manual quick validation in case of partial compliance
            if matter_number and not re.match(self.MATTER_NUMBER_PATTERN, matter_number):
                return self._failure_response(
                    f"Invalid matter number format: '{matter_number}'. Must be M##### (e.g., M12205)"
                )

            if document_type and document_type not in self.VALID_DOCUMENT_TYPES:
                return self._failure_response(
                    f"Invalid document type: '{document_type}'. Must be one of: {', '.join(sorted(self.VALID_DOCUMENT_TYPES))}"
                )

            if success and (not matter_number or not document_type):
                return self._failure_response(
                    "Missing matter number or document type in response"
                )

            return {
                "success": success,
                "matter_number": matter_number,
                "document_type": document_type,
                "error_message": error_message,
            }

        except json.JSONDecodeError as e:
            return self._failure_response(
                f"Failed to parse Gemini response as JSON. Response was: {response_text[:100]}"
            )

    def _failure_response(self, error_message: str) -> Dict:
        """Generate a standard failure response."""
        return {
            "success": False,
            "matter_number": None,
            "document_type": None,
            "error_message": error_message,
        }


if __name__ == "__main__":
    agent = IntakeAgent()
    tests = [
        ("Valid: Single Matter & Type", "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"),
        ("Valid: Another Request", "Please send me Exhibits from M12383."),
        ("Invalid: Multiple Matters", "Can you get documents from M12205 and M12383 for Key Documents?"),
        ("Invalid: Multiple Types", "I need Exhibits and Key Documents from M12205."),
        ("Invalid: Missing Type", "Can you help me with M12205?"),
        ("Invalid: Missing Matter", "I need the Other Documents please."),
        ("Invalid: Bad Matter Format", "Can you get Transcripts from M123?"),
        ("Invalid: Bad Type", "Please send me PDFs from M12205."),
    ]
    
    for name, email in tests:
        result = agent.parse_email(email)
        status = "✓" if result['success'] else "✗"
        print(f"{status} {name}")
        print(json.dumps(result, indent=2))
        print()

