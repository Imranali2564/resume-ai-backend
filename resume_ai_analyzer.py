import os
import fitz  # PyMuPDF
import docx
import pytesseract
from PIL import Image
import io
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ✅ Extract text from PDF
def extract_text_from_pdf(pdf_path):
    try:
        text = ""
        with fitz.open(pdf_path) as doc:
            for page in doc:
                text += page.get_text()
        return text.strip()
    except:
        return ""

# ✅ OCR fallback for PDF images
def extract_text_with_ocr(pdf_path):
    try:
        text = ""
        with fitz.open(pdf_path) as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=300)
                img_data = pix.tobytes("png")
                image = Image.open(io.BytesIO(img_data))
                text += pytesseract.image_to_string(image)
        return text.strip()
    except:
        return ""

# ✅ Extract text from DOCX
def extract_text_from_docx(docx_path):
    try:
        doc = docx.Document(docx_path)
        return "\n".join([para.text for para in doc.paragraphs])
    except:
        return ""

# ✅ ATS Compatibility Checker
def check_ats_compatibility(file_path):
    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".pdf":
        resume_text = extract_text_from_pdf(file_path) or extract_text_with_ocr(file_path)
    elif extension == ".docx":
        resume_text = extract_text_from_docx(file_path)
    else:
        return "❌ Unsupported file format."

    if not resume_text:
        return "⚠️ Could not extract text from resume."

    prompt = f"""
You are an expert in ATS (Applicant Tracking System) resume optimization.
Analyze the resume below and return an ATS compatibility report with positives and negatives.

Resume:
{resume_text}

ATS Report:
    """

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are an ATS evaluation assistant."},
            {"role": "user", "content": prompt}
        ]
    )

    return response.choices[0].message.content.strip()

# ✅ Main resume analyzer with atsfix toggle
def analyze_resume_with_openai(file_path, atsfix=False):
    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".pdf":
        resume_text = extract_text_from_pdf(file_path) or extract_text_with_ocr(file_path)
    elif extension == ".docx":
        resume_text = extract_text_from_docx(file_path)
    else:
        return {"suggestions": "❌ Unsupported file format."}

    if not resume_text:
        return {"suggestions": "⚠️ No extractable text found in resume."}

    if atsfix:
        prompt = (
            "You're an ATS optimization expert. Suggest only improvements related to ATS compatibility such as keyword optimization, "
            "removal of graphics or tables, formatting structure, and correct section headers. Do NOT mention grammar, achievements, or general tips.\n\n"
            f"Resume:\n{resume_text}\n\nATS Fix Suggestions:"
        )
    else:
        prompt = (
            "You're a professional resume reviewer. Suggest improvements in grammar, tone, clarity, and ATS compatibility. "
            "Provide detailed, actionable suggestions to improve the overall quality of the resume.\n\n"
            f"Resume:\n{resume_text}\n\nSuggestions:"
        )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful resume improvement assistant."},
            {"role": "user", "content": prompt}
        ]
    )

    return {"suggestions": response.choices[0].message.content.strip()}
