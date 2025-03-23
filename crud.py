from datetime import datetime
from collections import defaultdict
from telebot.types import Message
from model import SessionLocal, User, Wallet, Expense, SharedExpense
from sqlalchemy.orm import Session
from typing import Optional, List, Tuple

def get_or_create_user(session, telegram_id, username):
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id, username=username)
        session.add(user)
        session.commit()
    return user

def create_wallet(session, name, currency="EUR"):
    wallet = session.query(Wallet).filter_by(name=name).first()
    if not wallet:
        wallet = Wallet(name=name, currency=currency)
        session.add(wallet)
        session.commit()
    return wallet

def create_expense(session, user, wallet, amount, description, location, date, category):
    expense = Expense(
        user_id=user.id,
        wallet_id=wallet.id,
        amount=amount,
        description=description,
        location=location,
        date=date,
        category=category
    )
    session.add(expense)
    session.commit()
    return expense

def update_expense(session, expense_id, **kwargs):
    expense = session.query(Expense).filter_by(id=expense_id).first()
    if expense:
        for key, value in kwargs.items():
            setattr(expense, key, value)
        session.commit()
    return expense

def delete_expense(session, expense_id):
    expense = session.query(Expense).filter_by(id=expense_id).first()
    if expense:
        session.delete(expense)
        session.commit()
        return True
    return False

def share_expense(session, expense_id, shared_with_telegram_id):
    user_to_share = session.query(User).filter_by(telegram_id=shared_with_telegram_id).first()
    if not user_to_share:
        return None
    shared_entry = SharedExpense(expense_id=expense_id, shared_with_id=user_to_share.id, can_view=True)
    session.add(shared_entry)
    session.commit()
    return shared_entry

def revoke_share(session, shared_expense_id):
    shared_entry = session.query(SharedExpense).filter_by(id=shared_expense_id).first()
    if shared_entry:
        session.delete(shared_entry)
        session.commit()
        return True
    return False

def generate_monthly_report(session: Session, user_id: int, year: int, month: int) -> str:
    """Genera un report mensile delle spese"""
    try:
        # Recupera le transazioni del mese specifico
        start_date = datetime(year, month, 1)
        if month == 12:
            end_date = datetime(year + 1, 1, 1)
        else:
            end_date = datetime(year, month + 1, 1)

        transactions = session.query(Expense).join(Wallet).filter(
            Expense.user_id == user_id,
            Expense.date >= start_date,
            Expense.date < end_date
        ).all()

        # Prepara il report
        total_income = sum(tx.amount for tx in transactions if tx.amount > 0)
        total_expenses = sum(abs(tx.amount) for tx in transactions if tx.amount < 0)
        
        # Raggruppa per categoria
        categories = defaultdict(float)
        for tx in transactions:
            categories[tx.category] += tx.amount

        # Genera il messaggio
        month_name = start_date.strftime("%B")
        msg = f"📊 *Report {month_name} {year}*\n\n"
        msg += f"💰 Totale: {total_income - total_expenses:+,.2f}\n"
        msg += f"📥 Entrate: {total_income:,.2f}\n"
        msg += f"📤 Uscite: {total_expenses:,.2f}\n\n"
        
        msg += "*Dettaglio per categoria:*\n"
        for category, amount in categories.items():
            emoji = "📈" if amount >= 0 else "📉"
            msg += f"{emoji} {category}: {amount:+,.2f}\n"

        return msg

    except Exception as e:
        return f"⚠️ Errore: {str(e)}"

def generate_yearly_report(session: Session, user_id: int, year: int) -> str:
    """Genera un report annuale delle spese"""
    try:
        # Recupera le transazioni dell'anno specifico
        start_date = datetime(year, 1, 1)
        end_date = datetime(year + 1, 1, 1)

        transactions = session.query(Expense).join(Wallet).filter(
            Expense.user_id == user_id,
            Expense.date >= start_date,
            Expense.date < end_date
        ).all()

        # Prepara il report
        total_income = sum(tx.amount for tx in transactions if tx.amount > 0)
        total_expenses = sum(abs(tx.amount) for tx in transactions if tx.amount < 0)
        
        # Raggruppa per categoria e per mese
        categories = defaultdict(float)
        months = defaultdict(float)
        for tx in transactions:
            categories[tx.category] += tx.amount
            month_key = tx.date.strftime("%B")  # Nome del mese
            months[month_key] += tx.amount

        # Genera il messaggio
        msg = f"📊 *Report Anno {year}*\n\n"
        msg += f"💰 Totale: {total_income - total_expenses:+,.2f}\n"
        msg += f"📥 Entrate: {total_income:,.2f}\n"
        msg += f"📤 Uscite: {total_expenses:,.2f}\n\n"
        
        msg += "*Dettaglio per categoria:*\n"
        for category, amount in categories.items():
            emoji = "📈" if amount >= 0 else "📉"
            msg += f"{emoji} {category}: {amount:+,.2f}\n"
            
        msg += "\n*Andamento mensile:*\n"
        for month, amount in months.items():
            emoji = "📈" if amount >= 0 else "📉"
            msg += f"{emoji} {month}: {amount:+,.2f}\n"

        return msg

    except Exception as e:
        return f"⚠️ Errore: {str(e)}"
