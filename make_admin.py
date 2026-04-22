def make_admin(username):
    from app import get_db  # import inside function (important)

    conn = get_db()
    conn.execute(
        "UPDATE users SET is_admin=1 WHERE username=?",
        (username,)
    )
    conn.commit()
    conn.close()

    return f"{username} promoted to admin"