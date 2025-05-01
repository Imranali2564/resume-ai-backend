import os
import fitz  # PyMuPDF
import docx
import pytesseract
from PIL import Image
import io
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Extract text from PDF

def extract_text_from_pdf(file_path):
    try:
        with fitz.open(file_path) as doc:
            text = "\n".join([page.get_text() for page in doc])
        return text.strip()
    except Exception:
        return ""

# OCR if PDF fails

def extract_text_with_ocr(file_path):
    try:
        text = ""
        images = convert_pdf_to_images(file_path)
        for image in images:
            text += pytesseract.image_to_string(image)
        return text.strip()
    except Exception:
        return ""

# PDF to images

def convert_pdf_to_images(file_path):
    from pdf2image import convert_from_path
    return convert_from_path(file_path)

# Extract text from DOCX

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs]).strip()
    except Exception:
        return ""

# Analyze resume and give limited important suggestions

def analyze_resume_with_openai(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        text = extract_text_from_pdf(file_path) or extract_text_with_ocr(file_path)
    elif ext == ".docx":
        text = extract_text_from_docx(file_path)
    else:
        raise ValueError("Unsupported file format")

    if not text.strip():
        raise ValueError("Could not extract text from resume")

    prompt = f"""
You are an AI resume coach. Analyze the resume below and return only the 5 to 7 most important and impactful improvement suggestions.

Each suggestion must be:
- Focused on things that significantly affect job selection
- Easy to understand
- Written in 1-2 lines

Resume:
{text}

Suggestions:
"""

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful and precise resume assistant."},
            {"role": "user", "content": prompt}
        ]
    )
    content = response.choices[0].message.content.strip()
    return {"suggestions": content}

# ATS Compatibility Check

def check_ats_compatibility(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        text = extract_text_from_pdf(file_path) or extract_text_with_ocr(file_path)
    elif ext == ".docx":
        text = extract_text_from_docx(file_path)
    else:
        raise ValueError("Unsupported file format")

    if not text.strip():
        raise ValueError("Could not extract resume text")

    prompt = f"""
You're an ATS (Applicant Tracking System) expert. Analyze the resume below and return:

✅ What is good for ATS compatibility (like clean formatting, keywords, fonts, sections)
❌ What is bad or missing for ATS systems

Be clear and short. Mention only what matters for ATS.

Resume:
{text}

ATS Compatibility Report:
"""

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are an expert in ATS resume reviewing."},
            {"role": "user", "content": prompt}
        ]
    )
    report = response.choices[0].message.content.strip()
    return report
