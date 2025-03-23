from flask import Flask, request, jsonify
import datetime
from database import SessionLocal, engine, Base
from crud import (
    get_or_create_user,
    create_wallet,
    create_expense,
    update_expense,
    delete_expense,
    generate_monthly_report
)
from models import Expense

# Crea le tabelle se non esistono
Base.metadata.create_all(bind=engine)

app = Flask(__name__)

@app.route("/expenses", methods=["GET", "POST"])
def expenses():
    session = SessionLocal()
    if request.method == "POST":
        data = request.json
        # Richiesti: telegram_id, username, amount, description, category, location e opzionalmente date
        try:
            telegram_id = str(data["telegram_id"])
            username = data["username"]
            amount = float(data["amount"])
            description = data["description"]
            category = data["category"]
            location = data["location"]
            date_str = data.get("date")
            if date_str:
                date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            else:
                date = datetime.datetime.utcnow()
        except Exception as e:
            session.close()
            return jsonify({"error": f"Errore nei dati in input: {e}"}), 400

        user = get_or_create_user(session, telegram_id, username)
        wallet = create_wallet(session, "Principale", "EUR")
        expense = create_expense(session, user, wallet, amount, description, location, date, category)
        result = {"id": expense.id, "description": expense.description, "amount": expense.amount,
                  "category": expense.category, "location": expense.location,
                  "date": expense.date.strftime("%Y-%m-%d")}
        session.close()
        return jsonify(result), 201

    # GET: restituisce la lista di tutte le spese/entrate
    expenses_list = session.query(Expense).all()
    result = []
    for exp in expenses_list:
        result.append({
            "id": exp.id,
            "telegram_id": exp.user.telegram_id,
            "description": exp.description,
            "amount": exp.amount,
            "category": exp.category,
            "location": exp.location,
            "date": exp.date.strftime("%Y-%m-%d")
        })
    session.close()
    return jsonify(result)

@app.route("/expenses/<int:expense_id>", methods=["PUT", "DELETE"])
def expense_detail(expense_id):
    session = SessionLocal()
    if request.method == "PUT":
        data = request.json
        try:
            new_amount = float(data.get("amount"))
        except Exception as e:
            session.close()
            return jsonify({"error": f"Errore nei dati in input: {e}"}), 400
        expense = update_expense(session, expense_id, amount=new_amount)
        if expense:
            result = {"id": expense.id, "amount": expense.amount}
            session.close()
            return jsonify(result)
        else:
            session.close()
            return jsonify({"error": "Transazione non trovata"}), 404

    elif request.method == "DELETE":
        if delete_expense(session, expense_id):
            session.close()
            return jsonify({"message": "Transazione eliminata"})
        else:
            session.close()
            return jsonify({"error": "Transazione non trovata"}), 404

@app.route("/report", methods=["GET"])
def monthly_report():
    telegram_id = request.args.get("telegram_id")
    month = request.args.get("month", type=int)
    year = request.args.get("year", type=int)
    if not (telegram_id and month and year):
        return jsonify({"error": "telegram_id, month e year sono richiesti"}), 400
    session = SessionLocal()
    # Otteniamo l'utente in base al telegram_id
    from models import User
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        session.close()
        return jsonify({"error": "Utente non trovato"}), 404
    report = generate_monthly_report(session, user, month, year)
    result = []
    for key, total in report.items():
        category, currency, wallet_name = key
        result.append({
            "category": category,
            "wallet": wallet_name,
            "currency": currency,
            "total": total
        })
    session.close()
    return jsonify(result)

if __name__ == "__main__":
    app.run(debug=True)
