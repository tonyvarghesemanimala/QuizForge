from flask import Flask, render_template, request, redirect, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import time
import csv
import os


app = Flask(__name__)
app.secret_key = "change_this_in_production_xyz987"

# ---------------- HELPERS ----------------

def require_login():
    return "user_id" in session

def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_subjects():
    conn = get_db()
    data = conn.execute("SELECT id, name FROM subjects").fetchall()
    conn.close()
    return data

def get_sets(subject_id):
    conn = get_db()
    data = conn.execute("SELECT id, name FROM question_sets WHERE subject_id=?", (subject_id,)).fetchall()
    conn.close()
    return data

# ---------------- DATABASE ----------------
def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS question_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER,
        name TEXT
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER,
        set_id INTEGER,
        question_text TEXT,
        option_a TEXT,
        option_b TEXT,
        option_c TEXT,
        option_d TEXT,
        correct_option TEXT
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        set_id INTEGER,
        score REAL,
        correct INTEGER,
        wrong INTEGER,
        skipped INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        is_admin INTEGER DEFAULT 0
    )""")

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except:
        pass

    # Migration: add columns if missing
    for alter in [
        "ALTER TABLE questions ADD COLUMN page INTEGER",
        "ALTER TABLE results ADD COLUMN user_id INTEGER",
    ]:
        try:
            cursor.execute(alter)
        except:
            pass

    conn.commit()
    conn.close()


# ---------------- AUTH ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    error = None

    if request.method == "POST":
        u = request.form["username"].strip()
        p = request.form["password"]

        if not u or not p:
            error = "Username and password are required."
        else:
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO users (username, password) VALUES (?,?)",
                    (u, generate_password_hash(p))  # 🔐 FIXED
                )
                conn.commit()
                conn.close()
                return redirect("/login")
            except:
                conn.close()
                error = "Username already exists."

    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form["username"].strip()
        p = request.form["password"]
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (u,)
        ).fetchone()
        conn.close()
        if user and check_password_hash(user["password"], p):
            session["user_id"] = user["id"]
            session["username"] = u
            session["is_admin"] = user["is_admin"]
            return redirect("/")
        else:
            error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------------- HOME ----------------
@app.route("/")
def home():
    if not require_login():
        return redirect("/login")
    return render_template("subjects.html", subjects=get_subjects())


@app.route("/sets/<int:subject_id>")
def show_sets(subject_id):
    if not require_login():
        return redirect("/login")
    conn = get_db()
    subject = conn.execute("SELECT name FROM subjects WHERE id=?", (subject_id,)).fetchone()
    conn.close()
    return render_template("sets.html", sets=get_sets(subject_id),
                           subject_id=subject_id,
                           subject_name=subject["name"] if subject else "")


# ---------------- TEST MODE ----------------
@app.route("/start/<int:set_id>/<mode>")
def start_test(set_id, mode):
    if not require_login():
        return redirect("/login")
    session.pop("answers", None)
    session["mode"] = mode
    if mode == "timed":
        session["end_time"] = time.time() + (120 * 60)
    return redirect(f"/question/{set_id}/0")


@app.route("/question/<int:set_id>/<int:q_index>", methods=["GET", "POST"])
def show_question(set_id, q_index):
    if not require_login():
        return redirect("/login")

    conn = get_db()
    questions = conn.execute(
        "SELECT id, question_text, option_a, option_b, option_c, option_d FROM questions WHERE set_id=? ORDER BY id",
        (set_id,)
    ).fetchall()
    set_info = conn.execute("SELECT name FROM question_sets WHERE id=?", (set_id,)).fetchone()
    conn.close()

    if not questions:
        return redirect("/")

    if session.get("mode") == "timed" and time.time() > session.get("end_time", 0):
        return redirect(f"/result/{set_id}")

    if "answers" not in session:
        session["answers"] = {}

    if request.method == "POST":
        selected = request.form.get("answer")
        qid = str(questions[q_index]["id"])
        if selected:
            session["answers"][qid] = selected
        session.modified = True
        if q_index + 1 < len(questions):
            return redirect(f"/question/{set_id}/{q_index+1}")

    question = questions[q_index]
    q_ids = [str(q["id"]) for q in questions]

    return render_template("question.html",
        question=question,
        q_index=q_index,
        set_id=set_id,
        total=len(questions),
        answers=session["answers"],
        question_ids=q_ids,
        set_name=set_info["name"] if set_info else ""
    )


# AJAX: save answer without page reload
@app.route("/save_answer", methods=["POST"])
def save_answer():
    if not require_login():
        return jsonify({"ok": False}), 401
    data = request.get_json()
    if "answers" not in session:
        session["answers"] = {}
    qid = str(data.get("question_id"))
    answer = data.get("answer")
    if answer:
        session["answers"][qid] = answer
    else:
        session["answers"].pop(qid, None)
    session.modified = True
    return jsonify({"ok": True})


@app.route("/result/<int:set_id>")
def result(set_id):
    if not require_login():
        return redirect("/login")

    conn = get_db()

    correct_data = conn.execute("""
        SELECT id, question_text, option_a, option_b,
               option_c, option_d, correct_option
        FROM questions
        WHERE set_id=?
    """, (set_id,)).fetchall()

    set_info = conn.execute(
        "SELECT name FROM question_sets WHERE id=?",
        (set_id,)
    ).fetchone()

    answers = session.get("answers", {})

    correct = wrong = skipped = 0
    review = []

    for q in correct_data:
        qid = str(q["id"])
        user_ans = answers.get(qid)

        # 🧠 Determine status
        if user_ans is None:
            status = "skipped"
            skipped += 1
        elif user_ans == q["correct_option"]:
            status = "correct"
            correct += 1
        else:
            status = "wrong"
            wrong += 1

        # ✅ Improved review structure
        review.append({
            "question": q["question_text"],
            "options": {
                "A": q["option_a"],
                "B": q["option_b"],
                "C": q["option_c"],
                "D": q["option_d"]
            },
            "correct": q["correct_option"],
            "user": user_ans,
            "status": status
        })

    # 🎯 Score calculation
    score = correct - (wrong / 3)

    # 💾 Save result
    conn.execute("""
        INSERT INTO results (user_id, set_id, score, correct, wrong, skipped)
        VALUES (?,?,?,?,?,?)
    """, (
        session.get("user_id"),
        set_id,
        score,
        correct,
        wrong,
        skipped
    ))

    conn.commit()
    conn.close()

    # 🧹 Clear answers after submission
    session.pop("answers", None)

    return render_template(
        "result.html",
        score=round(score, 2),
        correct=correct,
        wrong=wrong,
        skipped=skipped,
        total=len(correct_data),
        review=review,
        set_name=set_info["name"] if set_info else ""
    )


@app.route("/history/<int:set_id>")
def history(set_id):
    if not require_login():
        return redirect("/login")

    conn = get_db()
    attempts = conn.execute("""
        SELECT score, correct, wrong, skipped,
        strftime('%d-%m-%Y %H:%M', created_at)
        FROM results
        WHERE set_id=? AND user_id=?
        ORDER BY created_at DESC
    """, (set_id, session.get("user_id"))).fetchall()
    set_info = conn.execute("SELECT name FROM question_sets WHERE id=?", (set_id,)).fetchone()
    conn.close()

    return render_template("history.html", attempts=attempts,
                           set_name=set_info["name"] if set_info else "")


# ---------------- LEARN MODE ----------------
@app.route("/learn/start/<int:set_id>")
def start_learn(set_id):
    if not require_login():
        return redirect("/login")

    session.pop("learn_queue", None)
    session.pop("learn_pdf", None)

    conn = get_db()
    row = conn.execute("""
        SELECT s.name FROM question_sets qs
        JOIN subjects s ON qs.subject_id=s.id
        WHERE qs.id=?
    """, (set_id,)).fetchone()
    conn.close()

    if not row:
        return redirect("/")

    session["learn_pdf"] = row["name"]
    return redirect(f"/learn/{set_id}/0")


@app.route("/learn/<int:set_id>/<int:q_index>")
def learn_mode(set_id, q_index):
    if not require_login():
        return redirect("/login")

    conn = get_db()
    all_q = conn.execute("""
        SELECT id, question_text, option_a, option_b,
               option_c, option_d, correct_option, page
        FROM questions WHERE set_id=? ORDER BY id
    """, (set_id,)).fetchall()
    set_info = conn.execute("SELECT name FROM question_sets WHERE id=?", (set_id,)).fetchone()
    conn.close()

    if "learn_queue" not in session or not session["learn_queue"]:
        session["learn_queue"] = [q["id"] for q in all_q]

    queue = session["learn_queue"]

    if not queue:
        session.pop("learn_queue", None)
        session.pop("learn_pdf", None)
        return render_template("learn_complete.html")

    if q_index >= len(queue):
        q_index = 0

    current_qid = queue[q_index]
    question = next((q for q in all_q if q["id"] == current_qid), None)

    if question is None:
        session.pop("learn_queue", None)
        return redirect(f"/learn/{set_id}/0")

    remaining = len(queue)
    total_questions = len(all_q)
    completed = total_questions - remaining
    learn_pdf = session.get("learn_pdf", "")

    return render_template("learn.html",
        question=question,
        q_index=q_index,
        set_id=set_id,
        total=len(queue),
        remaining=remaining,
        completed=completed,
        total_all=total_questions,
        learn_pdf=learn_pdf,
        set_name=set_info["name"] if set_info else ""
    )


@app.route("/learn_check", methods=["POST"])
def learn_check():
    if not require_login():
        return jsonify({"ok": False}), 401

    data = request.get_json()
    selected = data.get("selected")
    correct = data.get("correct")
    question_id = data.get("question_id")

    # If wrong, keep in queue; if correct, remove
    queue = session.get("learn_queue", [])
    if selected == correct:
        try:
            queue.remove(int(question_id))
        except ValueError:
            pass
    session["learn_queue"] = queue
    session.modified = True

    return jsonify({"ok": True, "remaining": len(queue)})


@app.route("/save_page", methods=["POST"])
def save_page():
    if not require_login():
        return jsonify({"ok": False}), 401

    data = request.get_json()
    question_id = data.get("question_id")
    page = data.get("page")

    conn = get_db()
    conn.execute("UPDATE questions SET page=? WHERE id=?", (page, question_id))
    conn.commit()
    conn.close()

    return jsonify({"ok": True})


# ---------------- SUPER LEARN ----------------
@app.route("/superlearn/start/<int:subject_id>", methods=["GET", "POST"])
def superlearn_start(subject_id):
    if not require_login():
        return redirect("/login")

    conn = get_db()
    subject = conn.execute("SELECT name FROM subjects WHERE id=?", (subject_id,)).fetchone()
    conn.close()

    if request.method == "POST":
        limit = int(request.form.get("limit", 25))
        session.pop("learn_queue", None)
        session["learn_pdf"] = subject["name"] if subject else ""

        conn = get_db()
        questions = conn.execute("""
            SELECT q.id FROM questions q
            JOIN question_sets qs ON q.set_id = qs.id
            WHERE qs.subject_id=?
            ORDER BY RANDOM() LIMIT ?
        """, (subject_id, limit)).fetchall()
        conn.close()

        session["learn_queue"] = [q["id"] for q in questions]
        session["superlearn_subject"] = subject_id
        return redirect(f"/superlearn/{subject_id}/0")

    return render_template("superlearn_select.html",
                           subject_id=subject_id,
                           subject_name=subject["name"] if subject else "")


@app.route("/superlearn/<int:subject_id>/<int:q_index>")
def superlearn_mode(subject_id, q_index):
    if not require_login():
        return redirect("/login")

    queue = session.get("learn_queue", [])

    if not queue:
        session.pop("learn_queue", None)
        return render_template("learn_complete.html")

    conn = get_db()
    all_ids = queue
    placeholders = ",".join("?" * len(all_ids))
    all_q = conn.execute(
        f"SELECT id, question_text, option_a, option_b, option_c, option_d, correct_option, page FROM questions WHERE id IN ({placeholders})",
        all_ids
    ).fetchall()
    conn.close()

    if q_index >= len(queue):
        q_index = 0

    current_qid = queue[q_index]
    question = next((q for q in all_q if q["id"] == current_qid), None)

    if question is None:
        return redirect(f"/superlearn/{subject_id}/0")

    learn_pdf = session.get("learn_pdf", "")
    total_questions = len(queue)

    return render_template("learn.html",
        question=question,
        q_index=q_index,
        set_id=subject_id,
        total=total_questions,
        remaining=total_questions,
        completed=0,
        total_all=total_questions,
        learn_pdf=learn_pdf,
        set_name="Super Learn",
        is_superlearn=True,
        subject_id=subject_id
    )

#-----------------ADMIN-----------------

@app.route("/admin")
def admin():
    if not require_login():
        return redirect("/login")

    if not session.get("is_admin"):
        return "Access denied"

    conn = get_db()

    subjects = conn.execute("SELECT id, name FROM subjects").fetchall()
    sets = conn.execute("SELECT id, name FROM question_sets").fetchall()

    conn.close()

    return render_template("admin.html",
                           subjects=subjects,
                           sets=sets)


@app.route("/admin/add_subject", methods=["POST"])
def add_subject():
    if not require_login() or not session.get("is_admin"):
        return "Access denied"

    name = request.form["name"].strip()

    # ❌ Empty check
    if not name:
        return "❌ Subject name cannot be empty"

    conn = get_db()

    # ❌ Duplicate check
    existing = conn.execute(
        "SELECT * FROM subjects WHERE name=?",
        (name,)
    ).fetchone()

    if existing:
        conn.close()
        return "❌ Subject already exists"

    # ✅ Insert
    conn.execute(
        "INSERT INTO subjects (name) VALUES (?)",
        (name,)
    )

    conn.commit()
    conn.close()

    return redirect("/admin")

@app.route("/admin/add_set", methods=["POST"])
def add_set():
    if not require_login() or not session.get("is_admin"):
        return "Access denied"

    name = request.form["name"].strip()
    subject_id = request.form["subject_id"]

    # ❌ Empty name check
    if not name:
        return "❌ Set name cannot be empty"

    # ❌ Subject check
    if not subject_id:
        return "❌ Subject must be selected"

    conn = get_db()

    # ❌ Check subject exists
    subject = conn.execute(
        "SELECT * FROM subjects WHERE id=?",
        (subject_id,)
    ).fetchone()

    if not subject:
        conn.close()
        return "❌ Invalid subject selected"

    # ❌ Duplicate check (same set under same subject)
    existing = conn.execute(
        "SELECT * FROM question_sets WHERE name=? AND subject_id=?",
        (name, subject_id)
    ).fetchone()

    if existing:
        conn.close()
        return "❌ Set already exists for this subject"

    # ✅ Insert
    conn.execute(
        "INSERT INTO question_sets (name, subject_id) VALUES (?,?)",
        (name, subject_id)
    )

    conn.commit()
    conn.close()

    return redirect("/admin")



@app.route("/admin/upload_csv", methods=["POST"])
def upload_csv():
    if not require_login() or not session.get("is_admin"):
        return "Access denied"

    file = request.files.get("file")
    subject_id = request.form.get("subject_id")
    set_id = request.form.get("set_id")

    # ❌ Validate inputs
    if not file or file.filename == "":
        return "❌ No file selected"

    if not subject_id or not set_id:
        return "❌ Subject and Set must be selected"

    conn = get_db()

    # ❌ Validate subject
    subject = conn.execute(
        "SELECT * FROM subjects WHERE id=?",
        (subject_id,)
    ).fetchone()

    if not subject:
        conn.close()
        return "❌ Invalid subject"

    # ❌ Validate set
    qset = conn.execute(
        "SELECT * FROM question_sets WHERE id=?",
        (set_id,)
    ).fetchone()

    if not qset:
        conn.close()
        return "❌ Invalid set"

    # ✅ Read file safely
    file.stream.seek(0)
    content = file.stream.read().decode("utf-8")

    if not content.strip():
        conn.close()
        return "❌ Empty CSV file"

    reader = csv.reader(content.splitlines())

    # ✅ Read header safely
    header = next(reader, None)

    # ✅ Detect format (with or without Question Number column)
    use_offset = 1 if header and len(header) >= 7 else 0

    inserted = 0

    for row in reader:
        # ❌ Skip bad rows
        if len(row) < (6 + use_offset):
            continue

        question = row[0 + use_offset].strip()

        # ❌ Skip empty questions
        if not question:
            continue

        conn.execute("""
            INSERT INTO questions
            (subject_id, set_id, question_text, option_a, option_b, option_c, option_d, correct_option)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            subject_id,
            set_id,
            row[0 + use_offset].strip(),  # question
            row[1 + use_offset].strip(),  # A
            row[2 + use_offset].strip(),  # B
            row[3 + use_offset].strip(),  # C
            row[4 + use_offset].strip(),  # D
            row[5 + use_offset].strip()   # correct
        ))

        inserted += 1

    conn.commit()
    conn.close()

    return f"✅ {inserted} questions uploaded successfully"



@app.route("/admin/upload_pdf", methods=["POST"])
def upload_pdf():
    if not require_login() or not session.get("is_admin"):
        return "Access denied"

    subject_id = request.form.get("subject_id")
    file = request.files.get("pdf")

    # ❌ Validate inputs
    if not subject_id:
        return "❌ Subject must be selected"

    if not file or file.filename == "":
        return "❌ No file selected"

    # ❌ Check file type
    if not file.filename.lower().endswith(".pdf"):
        return "❌ Only PDF files allowed"

    conn = get_db()

    subject = conn.execute(
        "SELECT name FROM subjects WHERE id=?",
        (subject_id,)
    ).fetchone()

    conn.close()

    # ❌ Subject check
    if not subject:
        return "❌ Invalid subject"

    subject_name = subject["name"].strip()

    # ✅ Make safe folder name
    safe_name = subject_name.replace(" ", "_")

    # 📁 folder path
    folder = os.path.join("static", "pdfs", safe_name)

    os.makedirs(folder, exist_ok=True)

    # 💾 save as text.pdf
    file_path = os.path.join(folder, "text.pdf")
    file.save(file_path)

    return "✅ PDF uploaded successfully"

from make_admin import make_admin

@app.route("/make_admin/<username>")
def run_make_admin(username):
    return make_admin(username)

# ---------------- MAIN ----------------
init_db()

if __name__ == "__main__":
    app.run()