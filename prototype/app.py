from flask import Flask, render_template, request, redirect, url_for, session

from database import initialize_database, authenticate_user


app = Flask(__name__)

app.secret_key = "eduos-prototype-secret-key"

initialize_database()


@app.route("/")
def index():

    if "roll_number" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():

    error = None

    if request.method == "POST":

        roll_number = request.form["roll_number"]
        password = request.form["password"]

        user = authenticate_user(roll_number, password)

        if user:

            session["roll_number"] = user["roll_number"]

            return redirect(url_for("dashboard"))

        error = "Invalid roll number or password."

    return render_template("login.html", error=error)


@app.route("/dashboard")
def dashboard():

    if "roll_number" not in session:
        return redirect(url_for("login"))

    return render_template(
        "dashboard.html",
        roll_number=session["roll_number"]
    )


@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
