import streamlit as st
import pdfplumber
import re
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="Exam Schedule Builder – Fixed Name Extraction", layout="wide")
st.title("📋 Exam Schedule Builder – Fixed Name Extraction")
st.markdown("Upload **Date Sheet PDF** and **Roll List PDF**. If extraction fails, expand the debug sections.")

# ------------------------------------------------------------
# 1. Parse Date Sheet (line‑by‑line, robust)
# ------------------------------------------------------------
def parse_date_sheet(pdf_file):
    exam_map = {}          # paper_id -> (date, subject, paper_code)
    current_date = None
    paper_id_pattern = re.compile(r'\b(\d{5})\b')
    code_pattern = re.compile(r'(\b[\w\.]+?-\d{3,4}\b)')

    all_lines = []
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                all_lines.extend(text.split('\n'))

    i = 0
    while i < len(all_lines):
        line = all_lines[i].strip()
        if not line:
            i += 1
            continue

        # Look for a date (dd.mm.yyyy or dd-mm-yyyy)
        date_match = re.search(r'(\d{2}[\.\-]\d{2}[\.\-]\d{4})', line)
        if date_match:
            current_date = date_match.group(1).replace('-', '.')
            # The same line might contain a paper entry, so we don't skip; we'll process it below

        # If we have a date, try to find paper IDs on this line
        if current_date:
            paper_ids = paper_id_pattern.findall(line)
            if paper_ids:
                # Extract paper code
                code_match = code_pattern.search(line)
                paper_code = code_match.group(1) if code_match else ""

                # Subject: everything before the first paper ID or paper code
                subject = line
                if paper_code and paper_code in line:
                    subject = line[:line.index(paper_code)].strip()
                elif paper_ids:
                    first_pid_pos = line.find(paper_ids[0])
                    subject = line[:first_pid_pos].strip()
                subject = re.sub(r'\s+', ' ', subject).strip(' -')

                for pid in paper_ids:
                    exam_map[pid] = (current_date, subject, paper_code)

        i += 1

    return exam_map

# ------------------------------------------------------------
# 2. Parse Roll List – Fixed Name Extraction
# ------------------------------------------------------------
def parse_roll_list(pdf_file):
    students = []
    paper_id_pattern = re.compile(r'\b(\d{5})\b')   # 5-digit paper IDs
    roll_pattern = re.compile(r'Roll\s*No\.?\s*(\d+)', re.IGNORECASE)
    reg_pattern = re.compile(r'Registration\s*No\.?\s*(\d+)', re.IGNORECASE)

    # Extract full text
    with pdfplumber.open(pdf_file) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    # Split by "Roll No" to get student blocks
    # We use a lookahead to keep the delimiter
    blocks = re.split(r'(Roll\s*No\.?\s*\d+)', full_text, flags=re.IGNORECASE)
    # blocks[0] is preamble, then each pair: delimiter+number, rest of block
    for i in range(1, len(blocks), 2):
        header = blocks[i]                 # e.g., "Roll No 251114511001"
        body = blocks[i+1] if i+1 < len(blocks) else ""

        # Extract roll number from header
        roll_match = roll_pattern.search(header)
        if not roll_match:
            continue
        roll_no = roll_match.group(1)

        # Find registration number line in body
        reg_match = reg_pattern.search(body)
        if reg_match:
            # Registration number found, now we need to locate the name
            # The name is usually the line immediately after the registration number line
            lines = body.split('\n')
            reg_line_idx = None
            for idx, line in enumerate(lines):
                if reg_pattern.search(line):
                    reg_line_idx = idx
                    break
            if reg_line_idx is not None and reg_line_idx + 1 < len(lines):
                # Next non-empty line after registration line is student name
                for offset in range(1, 5):
                    if reg_line_idx + offset < len(lines):
                        candidate = lines[reg_line_idx + offset].strip()
                        if candidate and not re.search(r'\d', candidate) and len(candidate) > 1:
                            student_name = candidate
                            break
                else:
                    student_name = "UNKNOWN"
            else:
                student_name = "UNKNOWN"
        else:
            # Fallback: if no registration number, try to find name near the roll number
            # Look for capitalized words after roll number line
            lines = (header + "\n" + body).split('\n')
            roll_line_idx = None
            for idx, line in enumerate(lines):
                if roll_pattern.search(line):
                    roll_line_idx = idx
                    break
            if roll_line_idx is not None and roll_line_idx + 1 < len(lines):
                student_name = lines[roll_line_idx + 1].strip()
            else:
                student_name = "UNKNOWN"

        # Clean student name (remove any extra spaces or stray characters)
        student_name = re.sub(r'\s+', ' ', student_name).strip()

        # Extract all paper IDs from the entire block (header+body)
        block_text = header + "\n" + body
        paper_ids = paper_id_pattern.findall(block_text)
        paper_ids = list(dict.fromkeys(paper_ids))   # unique, keep order

        if roll_no and paper_ids:
            students.append({
                'roll_no': roll_no,
                'student_name': student_name,
                'paper_ids': paper_ids
            })

    return students

# ------------------------------------------------------------
# 3. Merge
# ------------------------------------------------------------
def build_schedule(exam_map, students):
    rows = []
    for s in students:
        roll = s['roll_no']
        name = s['student_name']
        for pid in s['paper_ids']:
            if pid in exam_map:
                date, subject, code = exam_map[pid]
                rows.append({
                    'Roll No': roll,
                    'Student Name': name,
                    'Exam Date': date,
                    'Subject': subject,
                    'Paper Code': code,
                    'Paper ID': pid
                })
            else:
                rows.append({
                    'Roll No': roll,
                    'Student Name': name,
                    'Exam Date': 'NOT FOUND',
                    'Subject': 'UNKNOWN',
                    'Paper Code': '',
                    'Paper ID': pid
                })
    return pd.DataFrame(rows)

# ------------------------------------------------------------
# 4. UI
# ------------------------------------------------------------
col1, col2 = st.columns(2)
with col1:
    date_file = st.file_uploader("📅 Date Sheet PDF", type="pdf", key="date")
with col2:
    roll_file = st.file_uploader("🧑‍🎓 Roll List PDF", type="pdf", key="roll")

if date_file and roll_file:
    with st.spinner("🔍 Parsing PDFs..."):
        exam_map = parse_date_sheet(date_file)
        students = parse_roll_list(roll_file)
        df = build_schedule(exam_map, students)

    # Debug: show raw text snippets
    with st.expander("📄 Raw text from Date Sheet (first 1000 chars)"):
        with pdfplumber.open(date_file) as pdf:
            raw = "".join([p.extract_text() or "" for p in pdf.pages])[:1000]
        st.text(raw)

    with st.expander("📄 Raw text from Roll List (first 1000 chars)"):
        with pdfplumber.open(roll_file) as pdf:
            raw = "".join([p.extract_text() or "" for p in pdf.pages])[:1000]
        st.text(raw)

    with st.expander("📊 Extracted date sheet entries"):
        if exam_map:
            st.write(f"Found {len(exam_map)} paper entries. Sample:")
            st.json({k: exam_map[k] for k in list(exam_map)[:5]})
        else:
            st.error("No paper IDs found in date sheet. Check raw text above.")

    with st.expander("🧑‍🎓 Extracted students (first 10)"):
        if students:
            st.write(f"Found {len(students)} students. Sample:")
            # Show only roll and name for clarity
            sample = [{'roll_no': s['roll_no'], 'student_name': s['student_name'], 'paper_ids_count': len(s['paper_ids'])} for s in students[:10]]
            st.json(sample)
        else:
            st.error("No students found in roll list. Check raw text above.")

    if not df.empty:
        st.success(f"✅ Generated {len(df)} schedule rows for {df['Roll No'].nunique()} students.")
        st.subheader("📊 Preview (first 30)")
        st.dataframe(df.head(30), use_container_width=True)

        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Schedule')
        output.seek(0)
        st.download_button("📥 Download Excel", data=output,
                           file_name="exam_schedule.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.error("❌ No data could be extracted. Please check the debug info above.")
