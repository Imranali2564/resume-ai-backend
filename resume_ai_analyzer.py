from openai import OpenAI
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
import pytesseract
import docx
import os

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

POPPLER_PATH = r"C:\\Users\\Imran\\Downloads\\poppler-24.08.0\\Library\\bin"

def extract_text_from_pdf(file_path):
    try:
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except:
        return ""

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])
    except:
        return ""

def extract_text_with_ocr(file_path):
    try:
        images = convert_from_path(file_path, poppler_path=POPPLER_PATH)
        text = ""
        for img in images:
            ocr_text = pytesseract.image_to_string(img, config='--psm 6')
            text += ocr_text
        return text
    except:
        return ""

def analyze_resume_with_openai(file_path):
    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".pdf":
        resume_text = extract_text_from_pdf(file_path)
        if not resume_text.strip():
            resume_text = extract_text_with_ocr(file_path)
    elif extension == ".docx":
        resume_text = extract_text_from_docx(file_path)
    else:
        return {
            "suggestions": "❌ Unsupported file format. Please upload a valid PDF or DOCX file."
        }

    if not resume_text.strip():
        return {
            "suggestions": (
                "⚠️ Could not read any text from your file. Make sure it is not an image-only scan or overly designed layout.\n"
                "✅ Tip for Canva Users: Export your resume as a DOCX file, then convert it to PDF using Google Docs or MS Word."
            )
        }

    # ✅ New Improved Prompt
    prompt = (
        "You're an AI resume optimization expert. Analyze the resume below and return improvement suggestions in bullet points.\n"
        "Focus on:\n"
        "• Improving grammar and spelling\n"
        "• Using professional and action-oriented language\n"
        "• Fixing tone inconsistencies\n"
        "• Optimizing keywords for ATS (applicant tracking systems)\n"
        "• Removing weak or filler statements\n"
        "• Strengthening achievements and results\n"
        "• Rewording passive voice to active voice\n"
        "• Suggesting skills or sections to add if missing\n\n"
        f"Resume:\n{resume_text}\n\n"
        "Suggestions:"
    )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a top-tier AI resume reviewer and job application strategist."},
            {"role": "user", "content": prompt}
        ]
    )

    return {
        "suggestions": response.choices[0].message.content.strip()
    }
