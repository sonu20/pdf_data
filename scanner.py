import streamlit as st
import pdfplumber
import re
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="Exam Schedule Builder (Fixed)", layout="wide")
st.title("📋 Exam Schedule Builder – Enhanced Debugging")
st.markdown("Upload **Date Sheet PDF** and **Roll List PDF**. If extraction fails, expand the debug sections to see raw text.")

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
# 2. Parse Roll List (table + text fallback + brute force)
# ------------------------------------------------------------
def parse_roll_list(pdf_file, force_text=False):
    students = []
    paper_id_pattern = re.compile(r'\b(\d{5})\b')
    roll_pattern = re.compile(r'\b(\d{9,})\b')   # roll numbers are at least 9 digits

    # Extract all text first (for debugging and fallback)
    with pdfplumber.open(pdf_file) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    # Try table extraction unless forced to text mode
    if not force_text:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row:
                            continue
                        row_str = " ".join([str(c) for c in row if c])
                        # Look for a roll number (long digit string)
                        roll_match = roll_pattern.search(row_str)
                        if not roll_match:
                            continue
                        roll_no = roll_match.group(1)

                        # Try to get name from columns
                        name = ""
                        for cell in row:
                            cell_str = str(cell) if cell else ""
                            if cell_str and not roll_pattern.search(cell_str) and len(cell_str) > 2:
                                name = cell_str.strip()
                                break
                        if not name:
                            name = "UNKNOWN"

                        paper_ids = paper_id_pattern.findall(row_str)
                        paper_ids = list(dict.fromkeys(paper_ids))

                        if paper_ids:
                            students.append({
                                'roll_no': roll_no,
                                'student_name': name,
                                'paper_ids': paper_ids
                            })

    # If no students found via tables, use text chunking
    if not students:
        st.info("Table extraction gave no results – using text chunking.")
        # Split by "Roll No"
        chunks = re.split(r'(Roll\s*No\.?\s*)', full_text, flags=re.IGNORECASE)
        for i in range(1, len(chunks), 2):
            block = chunks[i] + chunks[i+1] if i+1 < len(chunks) else chunks[i]

            roll_match = roll_pattern.search(block)
            if not roll_match:
                continue
            roll_no = roll_match.group(1)

            # Extract name: look for "Name" field
            name_match = re.search(r'Name\s+([A-Z\s]+?)\s+(Father|SUBJECTS|Roll|$)', block, re.IGNORECASE)
            if name_match:
                student_name = name_match.group(1).strip()
            else:
                # Fallback: line after "Roll No"
                lines = block.split('\n')
                name = ""
                for idx, line in enumerate(lines):
                    if 'Roll No' in line and idx+1 < len(lines):
                        name = lines[idx+1].strip()
                        break
                student_name = name if name else "UNKNOWN"

            paper_ids = paper_id_pattern.findall(block)
            paper_ids = list(dict.fromkeys(paper_ids))

            if roll_no and paper_ids:
                students.append({
                    'roll_no': roll_no,
                    'student_name': student_name,
                    'paper_ids': paper_ids
                })

    # If still no students, try brute force: find all roll numbers and collect nearby paper IDs
    if not students:
        st.warning("Still no students – attempting brute force extraction.")
        # Find all roll numbers
        roll_matches = list(roll_pattern.finditer(full_text))
        for i, match in enumerate(roll_matches):
            roll_no = match.group(1)
            # Get a window of text around the roll number (e.g., 2000 chars before and after)
            start = max(0, match.start() - 1000)
            end = min(len(full_text), match.end() + 1000)
            context = full_text[start:end]
            paper_ids = paper_id_pattern.findall(context)
            paper_ids = list(dict.fromkeys(paper_ids))
            if paper_ids:
                # Try to find a name near the roll number
                # Look for a capitalized word before the roll number
                name_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', context[:match.start()-start])
                student_name = name_match.group(1) if name_match else "UNKNOWN"
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

force_text = st.checkbox("🔧 Force pure text mode (disable table extraction)")

if date_file and roll_file:
    with st.spinner("🔍 Parsing PDFs..."):
        exam_map = parse_date_sheet(date_file)
        students = parse_roll_list(roll_file, force_text=force_text)
        df = build_schedule(exam_map, students)

    # ---------- Debug: show raw text snippets ----------
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
            st.error("No paper IDs found in date sheet. Check the raw text above.")

    with st.expander("🧑‍🎓 Extracted students"):
        if students:
            st.write(f"Found {len(students)} students. Sample:")
            st.json(students[:3])
        else:
            st.error("No students found in roll list. Check the raw text above and try the 'Force text mode' option.")

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
        st.error("❌ No data could be extracted. Please check the raw text above and ensure the PDFs are text-based (not scanned).")
