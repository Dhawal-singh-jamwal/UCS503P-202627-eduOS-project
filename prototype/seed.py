from database import initialize_database, add_user


initialize_database()

users = [
    ("1000", "user1"),
    ("1001", "user2"),
    ("1002", "user3"),
    ("1003", "user4"),
    ("1004", "user5"),
    ("1005", "user6"),
    ("1006", "user7"),
    ("1007", "user8"),
    ("1008", "user9"),
    ("1009", "user10")
]

for roll_number, password in users:
    add_user(roll_number, password)

print("EduOS database initialized.")
print("10 users added.")
