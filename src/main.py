# Built from AgentMail's Auto-Reply Agent and Webhook Handler Documentation with custom multi-agent processing and Microsoft Graph API email sending.

import os
import threading
import requests
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, request, Response
import ngrok
from agentmail import AgentMail

from agents.intake_agent import IntakeAgent
from agents.navigate_agent import NavigateAgent
from agents.response_agent import ResponseAgent
from utils.zip import zip as zip_downloads

load_dotenv()

PORT = 8080
INBOX_ID = os.getenv("INBOX_ID", "senpilotdemoai@agentmail.to")
INBOX_USERNAME = os.getenv("INBOX_USERNAME", "auto-reply")
WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN")
MAX_DOCUMENTS = 2
MICROSOFT_API_TOKEN = os.getenv("MICROSOFT_API")

# Initialize Flask app and AgentMail client
app = Flask(__name__)
client = AgentMail(api_key=os.getenv("AGENTMAIL_KEY"))
processed_messages = set()  # Track processed message IDs to prevent duplicates


def generate_response(email_message: str) -> Dict[str, Any]:
    """Run Intake -> Navigate -> Zip -> Response and return Agent 3 output."""
    intake_agent = IntakeAgent()
    response_agent = ResponseAgent()

    intake_result = intake_agent.parse_email(email_message)
    if not intake_result.get("success"):
        return response_agent.build_response(
            email_text=email_message,
            navigate_result={
                "success": False,
                "error_message": intake_result.get("error_message") or "Intake parsing failed.",
            },
            zip_path=None,
        )

    matter_number, document_type = intake_result["matter_number"], intake_result["document_type"]

    navigate_agent = NavigateAgent(headless=True)
    navigate_result = navigate_agent.run_navigation(
        matter_number=matter_number,
        document_type=document_type,
        max_documents=MAX_DOCUMENTS,
    )

    if not navigate_result.get("success"):
        return response_agent.build_response(
            email_text=email_message,
            navigate_result=navigate_result,
            zip_path=None,
        )


    zip_path = zip_downloads(matter_number)
    
    return response_agent.build_response(
        email_text=email_message,
        navigate_result=navigate_result,
        zip_path=zip_path,
    )


def send_email(to_email: str, subject: str, body: str, attachment_path: str = None):
    headers = {
        'Authorization': f'Bearer {MICROSOFT_API_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    draft_payload = {
        "subject": subject,
        "body": {
            "contentType": "Text",
            "content": body
        },
        "toRecipients": [
            {"emailAddress": {"address": to_email}}
        ]
    }
    
    draft_response = requests.post(
        'https://graph.microsoft.com/v1.0/me/messages',
        json=draft_payload,
        headers=headers
    )
    
    if draft_response.status_code != 201:
        raise Exception(f"Draft creation failed: {draft_response.status_code} - {draft_response.text}")
    
    message_id = draft_response.json()['id']
    
    if attachment_path and os.path.exists(attachment_path):
        file_size = os.path.getsize(attachment_path)
        filename = os.path.basename(attachment_path)
        
        attachment_item = {
            "attachmentItem": {
                "attachmentType": "file",
                "name": filename,
                "size": file_size
            }
        }
        
        session_response = requests.post(
            f'https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments/createUploadSession',
            json=attachment_item,
            headers=headers
        )
        
        upload_url = session_response.json()['uploadUrl']
        
        chunk_size = 1024 * 1024 # 1 MB per upload split
        with open(attachment_path, 'rb') as f:
            file_data = f.read()
        
        start = 0
        while start < file_size:
            end = min(start + chunk_size, file_size)
            chunk = file_data[start:end]
            
            chunk_headers = {
                'Content-Length': str(len(chunk)),
                'Content-Range': f'bytes {start}-{end-1}/{file_size}'
            }
            
            requests.put(upload_url, data=chunk, headers=chunk_headers)
            
            start = end
    
    requests.post(
        f'https://graph.microsoft.com/v1.0/me/messages/{message_id}/send',
        headers=headers
    )

def setup_agentmail():
    """Create inbox and webhook with idempotency."""

    listener = ngrok.forward(PORT, authtoken_from_env=True)

    try:
        webhook = client.webhooks.create(
            url=f"{listener.url()}/webhook/agentmail",
            event_types=["message.received"],
            inbox_ids=[INBOX_ID],
            client_id=f"{INBOX_USERNAME}-webhook"
        )
        print(f"✓ Webhook created")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"✓ Webhook already exists")
        else:
            raise

    print(f"\n✓ Setup complete!")
    print(f"Inbox: {INBOX_ID}")
    print(f"Webhook: {listener.url()}/webhook/agentmail\n")

    return INBOX_ID, listener


def generate_reply(message):
    """Generate reply using the multi-agents setup (Intake -> Navigate -> Zip -> Response)."""
    subject = message.get('subject', '') or ''
    body = message.get('text', '') or message.get('preview', '') or ''
    email_message = f"Subject: {subject}\n\n{body}"
    result = generate_response(email_message)
    
    return result


def process_and_reply(from_field, subject, message):
    """Process incoming message and send reply in background."""
    # Extract sender email and name
    if '<' in from_field and '>' in from_field:
        sender_email = from_field.split('<')[1].split('>')[0].strip()
        sender_name = from_field.split('<')[0].strip()
        if not sender_name or ',' in sender_name:
            sender_name = sender_email.split('@')[0].title()
    else:
        sender_email = from_field.strip()
        sender_name = sender_email.split('@')[0].title() if '@' in sender_email else 'Friend'

    # Log incoming email
    print(f"Processing email from {sender_email}: {subject}")

    try:
        result = generate_reply(message)
        
        if not result.get('success'):
            error_msg = result.get('error_message', 'Unable to process your request.')
            reply_subject = f"Re: {subject}"
            reply_body = f"Hi {sender_name},\n\nI encountered an issue: {error_msg}\n\nPlease try again or contact support.\n\nBest regards,\nAgent"
            
            send_email(
                to_email=sender_email,
                subject=reply_subject,
                body=reply_body
            )
        else:
            reply_subject = result.get('subject', f"Re: {subject}")
            reply_body = result.get('body', 'Your documents are ready.')
            attachment_path = result.get('attachment_path')
            
            send_email(
                to_email=sender_email,
                subject=reply_subject,
                body=reply_body,
                attachment_path=attachment_path
            )
        
        print(f"✓ Reply sent to {sender_email} via Microsoft\n")
    except Exception as e:
        print(f"✗ Error: {e}\n")


@app.route('/webhook/agentmail', methods=['POST'])
def receive_webhook():
    """Webhook endpoint to receive incoming email notifications."""
    payload = request.json
    event_type = payload.get('type') or payload.get('event_type')

    # Ignore outgoing messages
    if event_type == 'message.sent':
        return Response(status=200)

    message = payload.get('message', {})
    message_id = message.get('message_id')
    from_field = message.get('from_', '')

    # Prevent duplicate
    if message_id in processed_messages:
        return Response(status=200)
    
    processed_messages.add(message_id)

    subject = message.get('subject', '(no subject)')

    # Process in background thread and return immediately
    thread = threading.Thread(
        target=process_and_reply,
        args=(from_field, subject, message)
    )
    thread.daemon = True
    thread.start()

    return Response(status=200)


if __name__ == '__main__':
    print("\n" + "="*60)
    print("AUTO-REPLY EMAIL AGENT")
    print("="*60 + "\n")

    inbox_id, listener = setup_agentmail()

    print(f"Agent is ready!")
    print(f"Send emails to: {inbox_id}")
    print(f"\nWaiting for incoming emails...\n")

    app.run(port=PORT)
