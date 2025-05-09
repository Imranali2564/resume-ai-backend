from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import uuid
import tempfile
import json
from werkzeug.utils import secure_filename
from resume_ai_analyzer import (
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_with_ocr,
    analyze_resume_with_openai,
    check_ats_compatibility,
    generate_section_content
)

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = tempfile.gettempdir()
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


def cleanup_file(file_path):
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        print(f"Cleanup failed: {str(e)}")


@app.route("/upload", methods=["POST"])
def upload_resume():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{uuid.uuid4()}_{filename}")
    file.save(file_path)

    # Call AI suggestion generator
    result = analyze_resume_with_openai(file_path)

    # Also try to extract sections
    try:
        if file_path.lower().endswith(".pdf"):
            text = extract_text_from_pdf(file_path) or extract_text_with_ocr(file_path)
        elif file_path.lower().endswith(".docx"):
            text = extract_text_from_docx(file_path)
        else:
            text = ""
        from resume_ai_analyzer import extract_resume_sections
        parsed_sections = extract_resume_sections(text)
    except Exception as e:
        parsed_sections = {}

    cleanup_file(file_path)
    result["sections"] = parsed_sections
    return jsonify(result)


@app.route("/resume-score", methods=["POST"])
def get_resume_score():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    if file.filename.lower().endswith(".pdf"):
        text = extract_text_from_pdf(file) or extract_text_with_ocr(file)
    elif file.filename.lower().endswith(".docx"):
        text = extract_text_from_docx(file)
    else:
        return jsonify({"error": "Unsupported file type"}), 400

    score = 70
    if "project" in text.lower():
        score += 10
    if "intern" in text.lower():
        score += 10
    if "certification" in text.lower():
        score += 5
    if "skill" in text.lower():
        score += 5

    score = min(score, 100)
    return jsonify({"score": score})


@app.route("/check-ats", methods=["POST"])
def ats_check():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    filename = secure_filename(file.filename)
    path = os.path.join(app.config["UPLOAD_FOLDER"], f"{uuid.uuid4()}_{filename}")
    file.save(path)

    report = check_ats_compatibility(path)
    cleanup_file(path)
    return jsonify({"ats_report": report})


@app.route("/fix-suggestion", methods=["POST"])
def fix_suggestion():
    try:
        data = request.get_json()
        suggestion = data.get("suggestion")
        section = data.get("section", "")
        section_content = data.get("sectionContent", "")

        if not suggestion:
            return jsonify({"error": "Missing suggestion"}), 400

        # Combine content if empty or invalid section passed
        if not section_content.strip():
            file = request.files.get("file")
            if not file:
                return jsonify({"error": "No resume provided"}), 400

            if file.filename.lower().endswith(".pdf"):
                full_text = extract_text_from_pdf(file) or extract_text_with_ocr(file)
            elif file.filename.lower().endswith(".docx"):
                full_text = extract_text_from_docx(file)
            else:
                return jsonify({"error": "Unsupported file format"}), 400

            fix_result = generate_section_content(suggestion, full_text)
            if "fixedContent" in fix_result:
                return jsonify(fix_result)
            else:
                return jsonify({"error": fix_result.get("error", "Unknown error")})
        else:
            # fallback to original method
            prompt = f"""
You are an AI resume assistant. Improve the following section of a resume based on this suggestion.

Suggestion: {suggestion}

Current Content:
{section_content}

Please return only the improved version of this section, no explanation.
            """
            from resume_ai_analyzer import get_openai_client
            client = get_openai_client()
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are an expert resume fixer."},
                    {"role": "user", "content": prompt}
                ]
            )
            fixed = response.choices[0].message.content.strip()
            return jsonify({"section": section, "fixedContent": fixed})
    except Exception as e:
        print("❌ Error in /fix-suggestion:", str(e))
        return jsonify({"error": "Failed to fix suggestion"}), 500


@app.route("/generate-cover-letter", methods=["POST"])
def generate_cover_letter():
    file = request.files.get("file")
    job_title = request.form.get("job_title")
    company_name = request.form.get("company_name")

    if not file or not job_title or not company_name:
        return jsonify({"error": "Missing inputs"}), 400

    if file.filename.lower().endswith(".pdf"):
        text = extract_text_from_pdf(file) or extract_text_with_ocr(file)
    elif file.filename.lower().endswith(".docx"):
        text = extract_text_from_docx(file)
    else:
        return jsonify({"error": "Unsupported file"}), 400

    prompt = f"""
You are a resume cover letter writer.
Based on the resume content below, generate a professional, ATS-friendly cover letter for the role of '{job_title}' at '{company_name}'.
Keep it concise, specific, and impactful.

Resume:
{text[:3000]}
    """

    from resume_ai_analyzer import get_openai_client
    client = get_openai_client()
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a cover letter writing assistant."},
            {"role": "user", "content": prompt}
        ]
    )

    result = response.choices[0].message.content.strip()
    return jsonify({"cover_letter": result})


@app.route("/download-cover-letter", methods=["POST"])
def download_cover_letter():
    data = request.get_json()
    content = data.get("cover_letter")
    if not content:
        return jsonify({"error": "No cover letter content provided"}), 400

    from docx import Document
    import io

    doc = Document()
    doc.add_paragraph(content)

    byte_io = io.BytesIO()
    doc.save(byte_io)
    byte_io.seek(0)

    return send_file(byte_io, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                     as_attachment=True, download_name="Cover_Letter.docx")
