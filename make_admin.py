import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

username = "tony"  # 👈 change this to your username

cursor.execute("UPDATE users SET is_admin=1 WHERE username=?", (username,))
conn.commit()

print("✅ User promoted to admin")

# verify
cursor.execute("SELECT username, is_admin FROM users")
for row in cursor.fetchall():
    print(row)

conn.close()