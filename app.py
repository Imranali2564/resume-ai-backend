from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
from openai import OpenAI
from resume_ai_analyzer import (
    analyze_resume_with_openai,
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_with_ocr
)

app = Flask(__name__)
CORS(app)

# Folder setup
UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Upload route (suggestions only)
@app.route('/upload', methods=['POST'])
def upload_resume():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    try:
        result = analyze_resume_with_openai(filepath)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ✅ New route to apply AI fix for individual suggestion
@app.route('/fix-suggestion', methods=['POST'])
def fix_suggestion():
    file = request.files.get('file')
    suggestion = request.form.get('suggestion')

    if not file or not suggestion:
        return jsonify({'error': 'File and suggestion are required'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    extension = os.path.splitext(filepath)[1].lower()

    # 1. Extract text from resume
    if extension == ".pdf":
        resume_text = extract_text_from_pdf(filepath)
        if not resume_text.strip():
            resume_text = extract_text_with_ocr(filepath)
    elif extension == ".docx":
        resume_text = extract_text_from_docx(filepath)
    else:
        return jsonify({'error': 'Unsupported file format. Please upload PDF or DOCX only'}), 400

    if not resume_text.strip():
        return jsonify({'error': 'Could not extract text from resume'}), 400

    # 2. AI fix based on suggestion
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    prompt = f"""
You are an expert resume editor. Apply the following fix to this resume:

Fix: "{suggestion}"

Resume:
{resume_text}

Now return the updated resume only, with the fix applied. Don't explain anything.
    """

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a resume fixing assistant."},
            {"role": "user", "content": prompt}
        ]
    )

    fixed_text = response.choices[0].message.content.strip()

    # 3. Save as downloadable file
    fixed_filename = f"fixed_resume_{uuid.uuid4().hex[:6]}.txt"
    fixed_filepath = os.path.join(STATIC_FOLDER, fixed_filename)

    with open(fixed_filepath, 'w', encoding='utf-8') as f:
        f.write(fixed_text)

    return jsonify({'download_url': f'/static/{fixed_filename}'})

# Run app on Render
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
