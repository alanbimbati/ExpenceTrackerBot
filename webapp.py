from flask import Flask, render_template
from model import SessionLocal, User, Expense, Wallet
from sqlalchemy import func, cast, Date
import json
import logging
from datetime import datetime, timedelta

app = Flask(__name__)
# Abilita il logging dettagliato
app.logger.setLevel(logging.DEBUG)

@app.route('/dashboard/<chat_id>')
def dashboard(chat_id):
    app.logger.info(f"Richiesta dashboard per chat_id: {chat_id}")
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == chat_id).first()
        if not user:
            app.logger.warning(f"Utente non trovato per chat_id: {chat_id}")
            return "Utente non trovato", 404

        # Calcola le spese totali
        total_expenses = session.query(func.sum(Expense.amount)).filter(
            Expense.user_id == user.id,
            Expense.amount < 0  # Solo le spese (importi negativi)
        ).scalar() or 0

        # Calcola le entrate totali
        total_income = session.query(func.sum(Expense.amount)).filter(
            Expense.user_id == user.id,
            Expense.amount > 0  # Solo le entrate (importi positivi)
        ).scalar() or 0

        # Calcola il saldo corrente
        current_balance = float(total_income) + float(total_expenses)  # total_expenses è già negativo

        # Spese per categoria (solo importi negativi)
        expenses_by_category = session.query(
            Expense.category,
            func.sum(Expense.amount).label('total')
        ).filter(
            Expense.user_id == user.id,
            Expense.amount < 0
        ).group_by(Expense.category).all()
        
        categories = []
        amounts = []
        for category, total in expenses_by_category:
            if category:  # Ignora le categorie nulle
                categories.append(category)
                amounts.append(abs(float(total)))  # Converti in positivo per la visualizzazione

        # Andamento temporale (ultimi 30 giorni)
        thirty_days_ago = datetime.now() - timedelta(days=30)
        
        # Query per ottenere tutte le transazioni (entrate e uscite)
        daily_transactions = session.query(
            func.strftime('%Y-%m-%d', Expense.date).label('date'),
            func.sum(Expense.amount).label('total')
        ).filter(
            Expense.user_id == user.id,
            Expense.date >= thirty_days_ago
        ).group_by(
            func.strftime('%Y-%m-%d', Expense.date)
        ).order_by(
            func.strftime('%Y-%m-%d', Expense.date)
        ).all()

        # Prepara i dati per il grafico temporale
        timeline_data = {
            'dates': [],
            'balances': []
        }

        # Calcola il saldo progressivo
        running_balance = 0
        for date_str, amount in daily_transactions:
            timeline_data['dates'].append(date_str)
            running_balance += float(amount)
            timeline_data['balances'].append(running_balance)

        return render_template(
            'dashboard.html',
            username=user.username or user.telegram_id,
            categories=json.dumps(categories),
            amounts=json.dumps(amounts),
            timeline_data=json.dumps(timeline_data),
            current_balance=current_balance,
            total_income=total_income,
            total_expenses=abs(total_expenses)
        )
    except Exception as e:
        app.logger.error(f"Errore durante l'elaborazione: {str(e)}", exc_info=True)
        return f"Si è verificato un errore: {str(e)}", 500
    finally:
        session.close()

if __name__ == '__main__':
    app.run(port=5000, debug=True) 