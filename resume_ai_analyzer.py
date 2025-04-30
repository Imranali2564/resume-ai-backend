from openai import OpenAI 
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
import pytesseract
import docx
import os

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
POPPLER_PATH = r"C:\\Users\\Imran\\Downloads\\poppler-24.08.0\\Library\\bin"

def extract_text_from_pdf(file_path):
    try:
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except Exception:
        return ""

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs]).strip()
    except Exception:
        return ""

def extract_text_with_ocr(file_path):
    try:
        images = convert_from_path(file_path, poppler_path=POPPLER_PATH)
        text = ""
        for img in images:
            text += pytesseract.image_to_string(img, config='--psm 6')
        return text.strip()
    except Exception:
        return ""

def get_stable_suggestions(resume_text):
    prompt = (
        "You're a professional resume reviewer. Analyze the resume and return improvement suggestions in clear, short bullet points.\n"
        "Do NOT repeat suggestions, do NOT give generic advice. Focus on actual improvement areas only.\n\n"
        f"Resume:\n{resume_text}\n\nSuggestions:"
    )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a precise and helpful AI resume improvement assistant."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()

def analyze_resume_with_openai(file_path):
    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".pdf":
        resume_text = extract_text_from_pdf(file_path)
        if not resume_text:
            resume_text = extract_text_with_ocr(file_path)
    elif extension == ".docx":
        resume_text = extract_text_from_docx(file_path)
    else:
        return {
            "suggestions": "❌ Unsupported file format. Please upload a valid PDF or DOCX file."
        }

    if not resume_text:
        return {
            "suggestions": (
                "⚠️ Could not read any text from your file. Make sure it is not an image-only scan or overly designed layout.\n"
                "✅ Tip for Canva Users: Export your resume as a DOCX file, then convert it to PDF using Google Docs or MS Word."
            )
        }

    suggestions = get_stable_suggestions(resume_text)
    return {"suggestions": suggestions}

def check_ats_compatibility(file_path):
    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".pdf":
        resume_text = extract_text_from_pdf(file_path)
        if not resume_text:
            resume_text = extract_text_with_ocr(file_path)
    elif extension == ".docx":
        resume_text = extract_text_from_docx(file_path)
    else:
        return "❌ Unsupported file format"

    if not resume_text:
        return "⚠️ Could not extract text from your resume."

    prompt = (
        "You're an expert ATS resume reviewer. Review the following resume and provide a bullet-point list "
        "with two sections:\n"
        "1. ✅ ATS-Friendly Elements (such as readable fonts, keyword usage, section clarity)\n"
        "2. ❌ ATS Blockers (like tables, graphics, headers/footers, fancy formatting)\n\n"
        f"Resume:\n{resume_text}\n\n"
        "Return only the two sections."
    )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a professional ATS resume checker."},
            {"role": "user", "content": prompt}
        ]
    )

    return response.choices[0].message.content.strip()
