import datetime
import csv
import pandas as pd
import io
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from config import TELEGRAM_API_TOKEN
from model import SessionLocal, engine, Base, Expense, User, Wallet, SharedAccess
from crud import (
    get_or_create_user,
    create_wallet,
    create_expense,
    update_expense,
    delete_expense,
    share_expense,
    revoke_share,
    generate_monthly_report,
    generate_yearly_report
)
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter
import random
from datetime import timedelta
from collections import defaultdict
from telebot.types import Message
import os
from configparser import ConfigParser

# Leggi la configurazione dal file config.ini
config = ConfigParser()
config.read('config.ini')

# Ottieni l'URL di ngrok dalla configurazione
NGROK_URL = config.get('WEBAPP', 'NGROK_URL', fallback='https://example.ngrok.io')

# Crea le tabelle nel DB (se non esistono)
Base.metadata.create_all(bind=engine)

bot = TeleBot(TELEGRAM_API_TOKEN)

# Numero di transazioni per pagina
PAGE_SIZE = 5

# Dizionario per memorizzare temporaneamente i dati di inserimento per chat
temp_expense_data = {}

if not NGROK_URL:
    print("⚠️ NGROK_URL non impostato. La webapp non funzionerà correttamente.")
    print("Esegui: export NGROK_URL='https://tuo-url-ngrok.ngrok.io'")
    NGROK_URL = "https://example.ngrok.io"  # URL di fallback

def create_navigation_keyboard():
    """Crea la keyboard permanente per la navigazione rapida"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        KeyboardButton("💰 Nuova Transazione"),
        KeyboardButton("📋 Lista Transazioni")
    )
    keyboard.row(
        KeyboardButton("📊 Report"),
        KeyboardButton("👥 Condivisioni")
    )
    keyboard.row(
        KeyboardButton("🏠 Menu Principale"),
        KeyboardButton("❓ Aiuto")
    )
    return keyboard

def get_username_from_message(message):
    """Helper per estrarre l'username da un messaggio"""
    if message.from_user.username:
        return message.from_user.username
    elif message.from_user.first_name:
        return message.from_user.first_name
    else:
        return str(message.from_user.id)

def get_username_from_callback(call):
    """Helper per estrarre l'username da un callback"""
    if call.from_user.username:
        return call.from_user.username
    elif call.from_user.first_name:
        return call.from_user.first_name
    else:
        return str(call.from_user.id)

@bot.message_handler(commands=["start"])
def send_welcome(message):
    """Invia il messaggio di benvenuto con i bottoni delle azioni principali"""
    username = get_username_from_message(message)
    session = SessionLocal()
    try:
        # Crea o recupera l'utente
        get_or_create_user(session, str(message.chat.id), username)
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("💰 Nuova Transazione", callback_data="new_transaction"),
            InlineKeyboardButton("📋 Lista Transazioni", callback_data="list_transactions")
        )
        markup.row(
            InlineKeyboardButton("📊 Report", callback_data="show_report"),
            InlineKeyboardButton("👥 Condividi", callback_data="share_menu")
        )
        
        bot.reply_to(message, 
                    "Benvenuto! Cosa vuoi fare?",
                    reply_markup=markup)
        
        # Aggiungi la keyboard permanente
        bot.send_message(
            message.chat.id,
            "🎯 Usa questi pulsanti per una navigazione rapida:",
            reply_markup=create_navigation_keyboard()
        )
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore: {str(e)}")
    finally:
        session.close()

# Aggiungi handler per i pulsanti della keyboard permanente
@bot.message_handler(func=lambda message: message.text in [
    "💰 Nuova Transazione",
    "📋 Lista Transazioni",
    "📊 Report",
    "👥 Condivisioni",
    "🏠 Menu Principale",
    "❓ Aiuto"
])
def handle_navigation_buttons(message):
    """Gestisce i click sui pulsanti della keyboard permanente"""
    try:
        username = get_username_from_message(message)
        if message.text == "💰 Nuova Transazione":
            start_new_transaction(message.chat.id, username=username)
            
        elif message.text == "📋 Lista Transazioni":
            show_transactions_list(message.chat.id, username=username)
            
        elif message.text == "📊 Report":
            show_report(message.chat.id, username=username)
            
        elif message.text == "👥 Condivisioni":
            show_share_menu(message.chat.id, username=username)
            
        elif message.text == "🏠 Menu Principale":
            show_main_menu(message.chat.id, username=username)
            
        elif message.text == "❓ Aiuto":
            show_help(message.chat.id, username=username)
            
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore: {str(e)}")

def start_new_transaction(chat_id, message_id=None, username=None):
    """Avvia il processo di nuova transazione"""
    try:
        text = ("📝 *Inserisci i dettagli della transazione* nel formato:\n\n"
                "`importo, descrizione, categoria, [data opzionale (YYYY-MM-DD)]`\n\n"
                "*Esempio:* `-10.5, Pranzo veloce, Alimentari, 2024-03-22`\n\n"
                "ℹ️ _Nota: usa un importo negativo per una spesa, positivo per un'entrata._")
        
        msg = bot.send_message(chat_id, text, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_expense_details)
            
    except Exception as e:
        error_msg = f"⚠️ Errore: {str(e)}"
        bot.send_message(chat_id, error_msg)

@bot.callback_query_handler(func=lambda call: call.data.startswith("list_transactions_"))
def handle_transactions_pagination(call: CallbackQuery):
    """Gestisce la paginazione della lista transazioni"""
    try:
        bot.answer_callback_query(call.id)
        offset = int(call.data.split("_")[-1])
        username = get_username_from_callback(call)
        show_transactions_list(call.message.chat.id, call.message.message_id, offset, username)
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")

def show_transactions_list(chat_id, message_id=None, offset=0, username=None):
    """Mostra la lista delle transazioni (proprie e condivise)"""
    try:
        session = SessionLocal()
        user = get_or_create_user(session, str(chat_id), username or str(chat_id))
        
        # Recupera le transazioni dell'utente
        own_transactions = session.query(Expense).join(Wallet).filter(
            Expense.user_id == user.id
        )
        
        # Recupera le transazioni condivise con l'utente
        shared_with_me = session.query(SharedAccess.owner_id).filter(
            SharedAccess.viewer_id == user.id
        ).all()
        shared_user_ids = [owner_id for (owner_id,) in shared_with_me]
        
        # Se ci sono condivisioni, aggiungi le transazioni condivise
        if shared_user_ids:
            shared_transactions = session.query(Expense).join(Wallet).filter(
                Expense.user_id.in_(shared_user_ids)
            )
            # Unisci le query
            all_transactions = own_transactions.union(shared_transactions)
        else:
            all_transactions = own_transactions
        
        # Ordina e applica offset e limit
        transactions = all_transactions.order_by(
            Expense.date.desc()
        ).offset(offset).limit(6).all()
        
        if not transactions:
            msg_text = "Nessuna transazione trovata."
            if message_id:
                bot.edit_message_text(msg_text, chat_id, message_id)
            else:
                bot.send_message(chat_id, msg_text)
            return
        
        # Usa solo le prime 5 transazioni per la visualizzazione
        display_transactions = transactions[:5]
        has_next = len(transactions) > 5
        has_prev = offset > 0
        
        # Costruisci il messaggio con le transazioni
        msg_text = f"*📋 Transazioni {offset+1}-{offset+len(display_transactions)}:*\n\n"
        
        # Raggruppa le transazioni per utente
        transactions_by_user = {}
        for tx in display_transactions:
            owner = session.query(User).get(tx.user_id)
            if owner.id not in transactions_by_user:
                transactions_by_user[owner.id] = {
                    'owner': owner,
                    'transactions': []
                }
            transactions_by_user[owner.id]['transactions'].append(tx)
        
        # Mostra le transazioni raggruppate per utente
        for user_data in transactions_by_user.values():
            owner = user_data['owner']
            is_own = owner.id == user.id
            owner_label = "📱 Le tue transazioni" if is_own else f"👤 Transazioni di {owner.username or owner.telegram_id}"
            msg_text += f"\n*{owner_label}*\n"
            
            for tx in user_data['transactions']:
                rounded_amount = round(tx.amount, 2)
                amount = f"{rounded_amount:+,.2f} {tx.wallet.currency}"
                emoji = "💰" if rounded_amount >= 0 else "💸"
                
                msg_text += (
                    f"{emoji} *{tx.date.strftime('%d/%m/%Y')}*\n"
                    f"└ Importo: {amount}\n"
                    f"└ Categoria: {tx.category}\n"
                    f"└ Descrizione: {tx.description}\n"
                    f"└ Luogo: {tx.location}\n"
                    f"└ Wallet: {tx.wallet.name}\n\n"
                )
        
        # Crea la tastiera inline con i bottoni di navigazione e azione
        markup = InlineKeyboardMarkup()
        row = []
        
        if has_prev:
            row.append(InlineKeyboardButton("⬅️ Precedenti", 
                      callback_data=f"list_transactions_{offset-5}"))
        if has_next:
            row.append(InlineKeyboardButton("Successive ➡️", 
                      callback_data=f"list_transactions_{offset+5}"))
        if row:
            markup.row(*row)
        
        # Aggiungi bottoni di azione solo per le proprie transazioni
        for tx in display_transactions:
            if tx.user_id == user.id:  # Solo per le proprie transazioni
                desc = tx.description[:15] + "..." if len(tx.description) > 15 else tx.description
                markup.row(
                    InlineKeyboardButton(f"✏️ {desc}", 
                                       callback_data=f"edit_tx_{tx.id}"),
                    InlineKeyboardButton(f"🗑️ {desc}", 
                                       callback_data=f"delete_tx_{tx.id}")
                )
        
        # Aggiungi bottoni per i report
        markup.row(
            InlineKeyboardButton("📊 Report Personale", callback_data="show_report_own"),
            InlineKeyboardButton("📊 Report Condivisi", callback_data="show_report_shared")
        )
        
        if message_id:
            bot.edit_message_text(msg_text, chat_id, message_id,
                                parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(chat_id, msg_text,
                           parse_mode="Markdown", reply_markup=markup)
            
    except Exception as e:
        error_msg = f"⚠️ Errore: {str(e)}"
        if message_id:
            bot.edit_message_text(error_msg, chat_id, message_id)
        else:
            bot.send_message(chat_id, error_msg)
    finally:
        if 'session' in locals():
            session.close()

@bot.message_handler(func=lambda message: message.text == "📊 Report")
def report_message(message):
    """Gestisce l'apertura del report dalla keyboard"""
    try:
        username = get_username_from_message(message)
        show_report(message.chat.id, username=username)
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == "show_report")
def report_callback(call: CallbackQuery):
    """Gestisce l'apertura del report da callback"""
    try:
        bot.answer_callback_query(call.id)
        username = get_username_from_callback(call)
        show_report(call.message.chat.id, call.message.message_id, username)
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")

def show_report(chat_id, message_id=None, username=None):
    """Mostra il report delle transazioni (proprie e condivise)"""
    try:
        session = SessionLocal()
        user = get_or_create_user(session, str(chat_id), username or str(chat_id))
        
        # Recupera le transazioni dell'utente
        own_transactions = session.query(Expense).join(Wallet).filter(
            Expense.user_id == user.id
        ).all()
        
        # Recupera le transazioni condivise con l'utente
        shared_with_me = session.query(SharedAccess.owner_id).filter(
            SharedAccess.viewer_id == user.id
        ).all()
        shared_user_ids = [owner_id for (owner_id,) in shared_with_me]
        
        # Dizionario per memorizzare i report per utente
        reports_by_user = {}
        
        # Analizza le transazioni proprie
        if own_transactions:
            reports_by_user[user.id] = {
                'username': "Le tue transazioni",
                'transactions': own_transactions,
                'total': sum(tx.amount for tx in own_transactions),
                'expenses': sum(tx.amount for tx in own_transactions if tx.amount < 0),
                'income': sum(tx.amount for tx in own_transactions if tx.amount > 0),
                'by_category': defaultdict(float)
            }
            for tx in own_transactions:
                reports_by_user[user.id]['by_category'][tx.category] += tx.amount
        
        # Analizza le transazioni condivise
        if shared_user_ids:
            for owner_id in shared_user_ids:
                shared_transactions = session.query(Expense).join(Wallet).filter(
                    Expense.user_id == owner_id
                ).all()
                
                if shared_transactions:
                    owner = session.query(User).get(owner_id)
                    reports_by_user[owner_id] = {
                        'username': f"Transazioni di {owner.username or owner.telegram_id}",
                        'transactions': shared_transactions,
                        'total': sum(tx.amount for tx in shared_transactions),
                        'expenses': sum(tx.amount for tx in shared_transactions if tx.amount < 0),
                        'income': sum(tx.amount for tx in shared_transactions if tx.amount > 0),
                        'by_category': defaultdict(float)
                    }
                    for tx in shared_transactions:
                        reports_by_user[owner_id]['by_category'][tx.category] += tx.amount
        
        # Genera il messaggio del report
        msg_text = "*📊 Report Dettagliato*\n\n"
        
        for user_data in reports_by_user.values():
            msg_text += f"*{user_data['username']}*\n"
            msg_text += f"💰 Totale: {user_data['total']:+,.2f}\n"
            msg_text += f"📥 Entrate: {user_data['income']:,.2f}\n"
            msg_text += f"📤 Uscite: {user_data['expenses']:,.2f}\n\n"
            
            msg_text += "*Dettaglio per categoria:*\n"
            for category, amount in user_data['by_category'].items():
                emoji = "📈" if amount >= 0 else "📉"
                msg_text += f"{emoji} {category}: {amount:+,.2f}\n"
            msg_text += "\n"
        
        # Crea la tastiera con le opzioni
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📅 Report Dettagliato", callback_data="detailed_report"),
            InlineKeyboardButton("🔙 Menu Principale", callback_data="main_menu")
        )
        markup.row(
            InlineKeyboardButton(
                "📊 Dashboard",
                web_app=WebAppInfo(url=f"{NGROK_URL}/dashboard/{chat_id}")
            )
        )
        
        if message_id:
            bot.edit_message_text(msg_text, chat_id, message_id,
                                parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(chat_id, msg_text,
                           parse_mode="Markdown", reply_markup=markup)
            
    except Exception as e:
        error_msg = f"⚠️ Errore: {str(e)}"
        if message_id:
            bot.edit_message_text(error_msg, chat_id, message_id)
        else:
            bot.send_message(chat_id, error_msg)
    finally:
        if 'session' in locals():
            session.close()

@bot.callback_query_handler(func=lambda call: call.data == "detailed_report")
def handle_detailed_report(call: CallbackQuery):
    """Gestisce la richiesta di report dettagliato"""
    try:
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "📅 *Report Dettagliato*\n\n"
            "Inserisci il periodo nel formato:\n"
            "- `YYYY` per report annuale (es. 2024)\n"
            "- `YYYY-MM` per report mensile (es. 2024-03)\n",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(call.message, process_report_period)
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")

def process_report_period(message: Message):
    """Processa il periodo inserito e genera il report appropriato"""
    try:
        period = message.text.strip()
        session = SessionLocal()
        username = message.from_user.username if message.from_user else str(message.chat.id)
        user = get_or_create_user(session, str(message.chat.id), username)
        
        # Verifica il formato del periodo
        if len(period) == 4:  # YYYY
            try:
                year = int(period)
                report = generate_yearly_report(session, user.id, year)
                bot.send_message(message.chat.id, report, parse_mode="Markdown")
            except ValueError:
                bot.reply_to(message, "⚠️ Formato anno non valido. Usa YYYY (es. 2024)")
        
        elif len(period) == 7 and period[4] == '-':  # YYYY-MM
            try:
                year, month = map(int, period.split('-'))
                if 1 <= month <= 12:
                    report = generate_monthly_report(session, user.id, year, month)
                    bot.send_message(message.chat.id, report, parse_mode="Markdown")
                else:
                    bot.reply_to(message, "⚠️ Mese non valido. Usa un numero da 1 a 12")
            except ValueError:
                bot.reply_to(message, "⚠️ Formato data non valido. Usa YYYY-MM (es. 2024-03)")
        
        else:
            bot.reply_to(
                message,
                "⚠️ Formato non valido.\nUsa:\n"
                "- YYYY per report annuale (es. 2024)\n"
                "- YYYY-MM per report mensile (es. 2024-03)"
            )
            
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore: {str(e)}")
    finally:
        session.close()

def show_main_menu(chat_id, message_id=None, username=None):
    """Mostra il menu principale"""
    try:
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("💰 Nuova Transazione", callback_data="new_transaction"),
            InlineKeyboardButton("📋 Lista Transazioni", callback_data="list_transactions")
        )
        markup.row(
            InlineKeyboardButton("📊 Report", callback_data="show_report"),
            InlineKeyboardButton("👥 Condividi", callback_data="share_menu")
        )
        
        text = "🏠 Menu Principale - Cosa vuoi fare?"
        
        if message_id:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)
        else:
            bot.send_message(chat_id, text, reply_markup=markup)
            
    except Exception as e:
        error_msg = f"⚠️ Errore: {str(e)}"
        if message_id:
            bot.edit_message_text(error_msg, chat_id, message_id)
        else:
            bot.send_message(chat_id, error_msg)

def show_help(chat_id, message_id=None, username=None):
    """Mostra il menu di aiuto"""
    try:
        help_text = (
            "*🤖 Comandi Disponibili:*\n\n"
            "💰 *Nuova Transazione*\n"
            "└ Inserisci una nuova spesa o entrata\n\n"
            "📋 *Lista Transazioni*\n"
            "└ Visualizza le ultime transazioni\n\n"
            "📊 *Report*\n"
            "└ Analisi dettagliata delle tue finanze\n\n"
            "👥 *Condivisioni*\n"
            "└ Gestisci la condivisione dei dati\n\n"
            "🏠 *Menu Principale*\n"
            "└ Torna al menu principale\n\n"
            "*Altri comandi:*\n"
            "/reset_db - Resetta il database\n"
            "/import_csv - Importa dati da CSV\n"
            "/share - Condividi con un utente\n"
            "/unshare - Revoca condivisioni\n"
            "/shared_reports - Vedi report condivisi"
        )
        
        if message_id:
            bot.edit_message_text(help_text, chat_id, message_id, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, help_text, parse_mode="Markdown")
            
    except Exception as e:
        error_msg = f"⚠️ Errore: {str(e)}"
        if message_id:
            bot.edit_message_text(error_msg, chat_id, message_id)
        else:
            bot.send_message(chat_id, error_msg)

# Aggiorna anche i callback handler per utilizzare le nuove funzioni
@bot.callback_query_handler(func=lambda call: call.data == "new_transaction")
def new_transaction_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id)
    username = get_username_from_callback(call)
    start_new_transaction(call.message.chat.id, call.message.message_id, username)

@bot.callback_query_handler(func=lambda call: call.data == "list_transactions")
def list_transactions_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id)
    username = get_username_from_callback(call)
    show_transactions_list(call.message.chat.id, call.message.message_id, username=username)

@bot.callback_query_handler(func=lambda call: call.data == "show_report")
def report_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id)
    username = get_username_from_callback(call)
    show_report(call.message.chat.id, call.message.message_id, username)

@bot.callback_query_handler(func=lambda call: call.data == "share_menu")
def share_menu_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id)
    username = get_username_from_callback(call)
    show_share_menu(call.message.chat.id, call.message.message_id, username)

@bot.callback_query_handler(func=lambda call: call.data == "main_menu")
def main_menu_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id)
    username = get_username_from_callback(call)
    show_main_menu(call.message.chat.id, call.message.message_id, username)

# ----------------------- INSERIMENTO MANUALE TRANSAZIONE ------------------------

@bot.callback_query_handler(func=lambda call: call.data == "add_expense")
def add_expense_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id, "✍️ Inserisci i dettagli della transazione!")
    msg = bot.send_message(call.message.chat.id,
        "📝 *Inserisci i dettagli della transazione* nel formato:\n\n"
        "`importo, descrizione, categoria, [data opzionale (YYYY-MM-DD)]`\n\n"
        "*Esempio:* `-10.5, Pranzo veloce, Alimentari, 2025-03-22`\n\n"
        "ℹ️ _Nota: usa un importo negativo per una spesa, positivo per un'entrata._",
        parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_expense_details)

@bot.callback_query_handler(func=lambda call: call.data == "new_transaction")
def new_transaction_callback(call: CallbackQuery):
    """Gestisce l'avvio di una nuova transazione"""
    try:
        bot.answer_callback_query(call.id)
        username = get_username_from_callback(call)
        msg = bot.send_message(
            call.message.chat.id,
            "📝 *Inserisci i dettagli della transazione* nel formato:\n\n"
            "`importo, descrizione, categoria, [data opzionale (YYYY-MM-DD)]`\n\n"
            "*Esempio:* `-10.5, Pranzo veloce, Alimentari, 2024-03-22`\n\n"
            "ℹ️ _Nota: usa un importo negativo per una spesa, positivo per un'entrata._",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_expense_details)
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")

def process_expense_details(message):
    """
    Primo step: parsing dei dettagli della transazione.
    """
    try:
        parts = [p.strip() for p in message.text.split(',')]
        if len(parts) < 3:
            raise ValueError("Inserisci almeno importo, descrizione e categoria.")
        
        amount = float(parts[0])
        description = parts[1]
        category = parts[2]
        if len(parts) >= 4 and parts[3]:
            date = datetime.datetime.strptime(parts[3], "%Y-%m-%d")
        else:
            date = datetime.datetime.utcnow()

        # Salva i dati parzialmente raccolti
        temp_expense_data[message.chat.id] = {
            "amount": amount,
            "description": description,
            "category": category,
            "date": date
        }

        # Chiedi il luogo
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📍 Invia Posizione", callback_data="send_location"),
            InlineKeyboardButton("✏️ Scrivi Luogo", callback_data="write_location")
        )
        
        bot.reply_to(
            message,
            "🌍 Come vuoi specificare il luogo?",
            reply_markup=markup
        )

    except ValueError as ve:
        bot.reply_to(
            message,
            f"⚠️ Errore nel formato: {str(ve)}\n"
            "Riprova usando il formato corretto:\n"
            "`importo, descrizione, categoria, [data opzionale]`"
        )
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore imprevisto: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data in ["send_location", "write_location"])
def handle_location_choice(call: CallbackQuery):
    """Gestisce la scelta del metodo di inserimento del luogo"""
    try:
        if call.data == "send_location":
            bot.answer_callback_query(call.id)
            bot.send_message(
                call.message.chat.id,
                "📍 Per favore, invia la tua posizione usando il pulsante 'Allega' di Telegram."
            )
            bot.register_next_step_handler(call.message, process_expense_location)
        else:  # write_location
            bot.answer_callback_query(call.id)
            bot.send_message(
                call.message.chat.id,
                "✏️ Scrivi il nome del luogo:"
            )
            bot.register_next_step_handler(call.message, process_expense_location)
            
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")

def process_expense_location(message):
    """
    Secondo step: acquisizione del luogo (testuale o posizione).
    """
    try:
        chat_id = message.chat.id
        data = temp_expense_data.get(chat_id)
        if not data:
            bot.reply_to(message, "⚠️ Dati non trovati. Riprova l'inserimento.")
            return

        if message.location:
            location = f"{message.location.latitude}, {message.location.longitude}"
        else:
            location = message.text.strip()
        
        session = SessionLocal()
        user = get_or_create_user(session, str(message.from_user.id),
                                message.from_user.username or message.from_user.first_name)
        
        # Chiedi la valuta
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("EUR", callback_data=f"currency_EUR_{location}"),
            InlineKeyboardButton("SAT", callback_data=f"currency_SAT_{location}")
        )
        
        bot.reply_to(
            message,
            "💱 Seleziona la valuta:",
            reply_markup=markup
        )
        
        session.close()
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore: {str(e)}")
        if 'session' in locals():
            session.close()

@bot.callback_query_handler(func=lambda call: call.data.startswith("currency_"))
def handle_currency_selection(call: CallbackQuery):
    """Gestisce la selezione della valuta e completa l'inserimento della transazione"""
    try:
        _, currency, location = call.data.split("_", 2)
        chat_id = call.message.chat.id
        data = temp_expense_data.get(chat_id)
        
        if not data:
            bot.answer_callback_query(call.id, "⚠️ Dati non trovati. Riprova l'inserimento.")
            return

        session = SessionLocal()
        user = get_or_create_user(session, str(call.from_user.id),
                                call.from_user.username or call.from_user.first_name)
        
        # Crea o recupera il wallet per la valuta selezionata
        wallet = create_wallet(session, f"Principale {currency}", currency)
        
        # Crea la transazione
        expense = create_expense(
            session,
            user,
            wallet,
            data["amount"],
            data["description"],
            location,
            data["date"],
            data["category"]
        )
        
        # Pulisci i dati temporanei
        temp_expense_data.pop(chat_id, None)
        
        # Conferma l'inserimento
        bot.answer_callback_query(call.id, "✅ Transazione inserita con successo!")
        bot.edit_message_text(
            f"✅ Transazione inserita:\n\n"
            f"💰 Importo: {expense.amount} {wallet.currency}\n"
            f"📝 Descrizione: {expense.description}\n"
            f"🏷️ Categoria: {expense.category}\n"
            f"📅 Data: {expense.date.strftime('%d/%m/%Y')}\n"
            f"📍 Luogo: {expense.location}",
            call.message.chat.id,
            call.message.message_id
        )
        
        session.close()
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")
        if 'session' in locals():
            session.close()

# ----------------------- LISTA TRANSAZIONI CON PAGINAZIONE ------------------------

def send_expenses_page(chat_id, user, page: int):
    session = SessionLocal()
    expenses = (session.query(Expense)
                .filter_by(user_id=user.id)
                .order_by(Expense.date.desc())
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
                .all())
    if not expenses:
        bot.send_message(chat_id, "📭 Non ci sono transazioni per questa pagina.")
        session.close()
        return

    for exp in expenses:
        text = (f"🔖 *ID:* `{exp.id}`\n"
                f"📅 *Data:* {exp.date.strftime('%Y-%m-%d')}\n"
                f"📝 *Descrizione:* {exp.description}\n"
                f"💰 *Importo:* {exp.amount} {exp.wallet.currency}\n"
                f"🏷️ *Categoria:* {exp.category}\n"
                f"📍 *Luogo:* {exp.location}")
        markup = InlineKeyboardMarkup()
        markup.row_width = 2
        markup.add(
            InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{exp.id}"),
            InlineKeyboardButton("🗑️ Elimina", callback_data=f"delete_{exp.id}")
        )
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    # Bottoni di navigazione per la paginazione e per il download CSV
    nav_markup = InlineKeyboardMarkup()
    if page > 0:
        nav_markup.add(InlineKeyboardButton("<< Precedente", callback_data=f"list_expenses_page_{page-1}"))
    session = SessionLocal()
    count = session.query(Expense).filter_by(user_id=user.id).count()
    session.close()
    if (page + 1) * PAGE_SIZE < count:
        nav_markup.add(InlineKeyboardButton("Successiva >>", callback_data=f"list_expenses_page_{page+1}"))
    nav_markup.add(InlineKeyboardButton("Scarica CSV", callback_data="download_csv"))
    bot.send_message(chat_id, f"Pagina {page + 1}", reply_markup=nav_markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("list_expenses_page_"))
def list_expenses_callback(call: CallbackQuery):
    page = int(call.data.split("_")[-1])
    session = SessionLocal()
    user = get_or_create_user(session, str(call.from_user.id),
                              call.from_user.username or call.from_user.first_name)
    session.close()
    send_expenses_page(call.message.chat.id, user, page)
    bot.answer_callback_query(call.id)

# ----------------------- DOWNLOAD CSV ------------------------

@bot.callback_query_handler(func=lambda call: call.data == "download_csv")
def download_csv_callback(call: CallbackQuery):
    session = SessionLocal()
    user = get_or_create_user(session, str(call.from_user.id),
                              call.from_user.username or call.from_user.first_name)
    expenses = (session.query(Expense)
                .filter_by(user_id=user.id)
                .order_by(Expense.date.desc())
                .all())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Data", "Descrizione", "Importo", "Categoria", "Luogo", "Valuta"])
    for exp in expenses:
        writer.writerow([
            exp.id,
            exp.date.strftime('%Y-%m-%d'),
            exp.description,
            exp.amount,
            exp.category,
            exp.location,
            exp.wallet.currency
        ])
    session.close()
    csv_data = output.getvalue()
    output.close()
    csv_bytes = io.BytesIO(csv_data.encode('utf-8'))
    csv_bytes.name = "transazioni.csv"
    bot.send_document(call.message.chat.id, document=csv_bytes)
    bot.answer_callback_query(call.id)

# ----------------------- MODIFICA ED ELIMINAZIONE TRANSAZIONI ------------------------

@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_"))
def delete_expense_callback(call: CallbackQuery):
    expense_id = int(call.data.split("_")[-1])
    session = SessionLocal()
    if delete_expense(session, expense_id):
        bot.answer_callback_query(call.id, f"Transazione {expense_id} eliminata.")
        bot.send_message(call.message.chat.id, f"Transazione {expense_id} eliminata.")
    else:
        bot.answer_callback_query(call.id, "Transazione non trovata.")
    session.close()

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_"))
def edit_expense_callback(call: CallbackQuery):
    expense_id = int(call.data.split("_")[-1])
    msg = bot.send_message(call.message.chat.id,
                           f"Inserisci il nuovo importo per la transazione {expense_id}:")
    bot.register_next_step_handler(msg, process_edit_expense, expense_id)
    bot.answer_callback_query(call.id)

def process_edit_expense(message, expense_id):
    try:
        new_amount = float(message.text.strip())
    except ValueError:
        bot.reply_to(message, "Importo non valido. Riprova.")
        return
    session = SessionLocal()
    expense = update_expense(session, expense_id, amount=new_amount)
    if expense:
        bot.reply_to(message, f"Transazione {expense.id} aggiornata a {expense.amount}")
    else:
        bot.reply_to(message, "Transazione non trovata.")
    session.close()

# ----------------------- ALTRI COMANDI (share, revoke, report, ecc.) ------------------

@bot.callback_query_handler(func=lambda call: call.data == "share_expense")
def share_expense_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id, "Inserisci il Telegram ID dell'utente con cui vuoi condividere la transazione:")
    msg = bot.send_message(call.message.chat.id, "Inserisci il Telegram ID dell'utente con cui vuoi condividere la transazione:")
    bot.register_next_step_handler(msg, process_share_expense)

def process_share_expense(message):
    session = SessionLocal()
    user = get_or_create_user(session, str(message.from_user.id),
                              message.from_user.username or message.from_user.first_name)
    shared_entry = share_expense(session, message.text.strip(), user.id)
    if shared_entry:
        bot.reply_to(message, f"Transazione condivisa con {message.text.strip()}")
    else:
        bot.reply_to(message, "Impossibile condividere la transazione. Verifica il Telegram ID.")
    session.close()

@bot.callback_query_handler(func=lambda call: call.data == "revoke_share")
def revoke_share_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id, "Inserisci l'ID della transazione da revocare:")
    msg = bot.send_message(call.message.chat.id, "Inserisci l'ID della transazione da revocare:")
    bot.register_next_step_handler(msg, process_revoke_share)

def process_revoke_share(message):
    session = SessionLocal()
    shared_entry = revoke_share(session, message.text.strip())
    if shared_entry:
        bot.reply_to(message, "Condivisione revocata.")
    else:
        bot.reply_to(message, "Transazione non trovata o non condivisa.")

@bot.callback_query_handler(func=lambda call: call.data == "monthly_report")
def report_callback(call: CallbackQuery):
    insert = "Inserisci il periodo per il report:\n- AAAA per report annuale (es. 2024)\n- MM/AAAA per report mensile (es. 3/2024)\n- GG/MM/AAAA per report giornaliero (es. 1/3/2024)"
    bot.answer_callback_query(call.id, insert)
    msg = bot.send_message(call.message.chat.id, insert)
    bot.register_next_step_handler(msg, process_report)

def process_report(message: Message):
    try:
        period_input = message.text.strip()
        parts = period_input.split("/")
        
        # Determina granularità e intervallo temporale
        if len(parts) == 1:
            # Formato: "2024" → annuale
            granularity = "yearly"
            year = int(parts[0])
            start_date = datetime.datetime(year, 1, 1)
            end_date = datetime.datetime(year + 1, 1, 1)
        elif len(parts) == 2:
            # Possibilità: "*/2025" oppure "3/2025"
            if parts[0] == "*":
                granularity = "yearly"
                year = int(parts[1])
                start_date = datetime.datetime(year, 1, 1)
                end_date = datetime.datetime(year + 1, 1, 1)
            else:
                granularity = "monthly"
                month = int(parts[0])
                year = int(parts[1])
                start_date = datetime.datetime(year, month, 1)
                # Calcola il primo giorno del mese successivo
                if month == 12:
                    end_date = datetime.datetime(year + 1, 1, 1)
                else:
                    end_date = datetime.datetime(year, month + 1, 1)
        elif len(parts) == 3:
            # Possibilità: "*/1/2025", "*/*/2025" oppure "1/3/2025"
            if parts[0] == "*" and parts[1] == "*":
                granularity = "yearly"
                year = int(parts[2])
                start_date = datetime.datetime(year, 1, 1)
                end_date = datetime.datetime(year + 1, 1, 1)
            elif parts[0] == "*":
                # Esempio: "*/1/2025" → mensile (tutti i giorni di un mese)
                granularity = "monthly"
                month = int(parts[1])
                year = int(parts[2])
                start_date = datetime.datetime(year, month, 1)
                if month == 12:
                    end_date = datetime.datetime(year + 1, 1, 1)
                else:
                    end_date = datetime.datetime(year, month + 1, 1)
            else:
                # Formato: "1/3/2025" → giornaliero
                granularity = "daily"
                day = int(parts[0])
                month = int(parts[1])
                year = int(parts[2])
                start_date = datetime.datetime(year, month, day)
                end_date = start_date + datetime.timedelta(days=1)
        else:
            bot.reply_to(message, "Formato periodo non valido. Riprova.")
            return

        def get_period_key(exp_date: datetime.datetime):
            if granularity == "yearly":
                return f"{exp_date.year}"
            elif granularity == "monthly":
                return f"{exp_date.month:02d}/{exp_date.year}"
            elif granularity == "daily":
                return f"{exp_date.day:02d}/{exp_date.month:02d}/{exp_date.year}"

        session = SessionLocal()
        user = get_or_create_user(session, str(message.from_user.id),
                                message.from_user.username or message.from_user.first_name)
        
        # Recupera le transazioni separate per valuta
        transactions_eur = session.query(Expense).join(Wallet).filter(
            Expense.user_id == user.id,
            Expense.date >= start_date,
            Expense.date < end_date,
            Wallet.currency == "EUR"
        ).all()

        transactions_sat = session.query(Expense).join(Wallet).filter(
            Expense.user_id == user.id,
            Expense.date >= start_date,
            Expense.date < end_date,
            Wallet.currency == "SAT"
        ).all()
        
        if not transactions_eur and not transactions_sat:
            bot.send_message(message.chat.id, "Nessuna transazione trovata per il periodo specificato.")
            session.close()
            return

        # Funzione helper per generare il report per una valuta
        def generate_currency_report(transactions, currency):
            overall = defaultdict(lambda: [0, 0])
            by_category = defaultdict(lambda: [0, 0])
            by_wallet = defaultdict(lambda: [0, 0])
            
            for exp in transactions:
                key = get_period_key(exp.date)
                overall[key][0] += exp.amount
                overall[key][1] += 1
                
                by_category[(key, exp.category)][0] += exp.amount
                by_category[(key, exp.category)][1] += 1
                
                wallet_name = exp.wallet.name if exp.wallet else "N/D"
                by_wallet[(key, wallet_name)][0] += exp.amount
                by_wallet[(key, wallet_name)][1] += 1

            total_amount = sum(total for total, _ in overall.values())
            total_transactions = sum(count for _, count in overall.values())

            report = (
                f"💰 *REPORT {currency}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📈 *RIEPILOGO GENERALE*\n"
                f"• Totale movimenti: {total_amount:.2f} {currency}\n"
                f"• Numero transazioni: {total_transactions}\n"
            )
            
            if total_transactions > 0:
                report += f"• Media per transazione: {(total_amount/total_transactions):.2f} {currency}\n"

            report += "\n*📅 DETTAGLIO PER PERIODO*\n"
            for period in sorted(overall.keys()):
                total, count = overall[period]
                report += f"• {period}: {total:.2f} {currency} ({count} trans.)\n"

            # Analisi categorie
            category_totals = defaultdict(lambda: [0, 0])
            for (_, cat), (tot, cnt) in by_category.items():
                category_totals[cat][0] += tot
                category_totals[cat][1] += cnt

            report += "\n*🏷️ ANALISI PER CATEGORIA*\n"
            sorted_categories = sorted(
                category_totals.items(),
                key=lambda x: abs(x[1][0]),
                reverse=True
            )
            
            for cat, (tot, cnt) in sorted_categories:
                percentage = (tot / total_amount * 100) if total_amount != 0 else 0
                report += f"• {cat}: {tot:.2f} {currency} ({cnt} trans., {percentage:.1f}%)\n"

            return report, overall, category_totals, by_wallet

        # Genera report separati per ogni valuta
        if transactions_eur:
            report_eur, overall_eur, categories_eur, wallets_eur = generate_currency_report(transactions_eur, "EUR")
            bot.send_message(message.chat.id, report_eur, parse_mode="Markdown")
            
            bot.send_message(message.chat.id, "📊 Generazione grafici EUR...")
            try:
                image_buffers = create_report_charts(overall_eur, categories_eur, wallets_eur, "EUR")
                for buf in image_buffers:
                    bot.send_photo(message.chat.id, buf)
                    buf.close()
            except Exception as e:
                bot.send_message(message.chat.id, f"⚠️ Errore nella generazione dei grafici EUR: {str(e)}")

        if transactions_sat:
            report_sat, overall_sat, categories_sat, wallets_sat = generate_currency_report(transactions_sat, "SAT")
            bot.send_message(message.chat.id, report_sat, parse_mode="Markdown")
            
            bot.send_message(message.chat.id, "📊 Generazione grafici SAT...")
            try:
                image_buffers = create_report_charts(overall_sat, categories_sat, wallets_sat, "SAT")
                for buf in image_buffers:
                    bot.send_photo(message.chat.id, buf)
                    buf.close()
            except Exception as e:
                bot.send_message(message.chat.id, f"⚠️ Errore nella generazione dei grafici SAT: {str(e)}")

    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Errore nell'elaborazione del report: {str(e)}")
    finally:
        if 'session' in locals():
            session.close()

def create_report_charts(overall, category_totals, wallet_totals, currency):
    """
    Crea i grafici per il report finanziario.
    Restituisce una lista di buffer di immagini.
    """
    plt.style.use('ggplot')
    image_buffers = []
    
    # Funzione helper per formattare gli importi
    def currency_formatter(x, p):
        return f'{x:,.0f} {currency}'

    try:
        # 1. Grafico andamento temporale
        plt.figure(figsize=(12, 6))
        periods = sorted(overall.keys())
        amounts = [float(overall[p][0]) for p in periods]  # Converti in float
        
        plt.plot(periods, amounts, marker='o', linewidth=2, markersize=8)
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.title(f'📈 Andamento Temporale ({currency})', pad=20, fontsize=14)
        plt.xlabel('Periodo', fontsize=12)
        plt.ylabel(f'Importo ({currency})', fontsize=12)
        plt.xticks(rotation=45)
        plt.gca().yaxis.set_major_formatter(FuncFormatter(currency_formatter))
        
        # Aggiungi etichette dei valori
        for i, amount in enumerate(amounts):
            plt.annotate(f'{amount:,.0f} {currency}', 
                        (periods[i], amount),
                        textcoords="offset points",
                        xytext=(0,10),
                        ha='center')
        
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
        buf.seek(0)
        image_buffers.append(buf)
        plt.close()

        # 2. Grafico a torta delle categorie
        if category_totals:
            plt.figure(figsize=(10, 10))
            # Converti le tuple in stringhe per le categorie
            categories_dict = {}
            for (period, cat), (amount, count) in category_totals.items():
                if cat not in categories_dict:
                    categories_dict[cat] = [0, 0]
                categories_dict[cat][0] += float(amount)
                categories_dict[cat][1] += count

            categories = list(categories_dict.keys())
            amounts = [abs(total[0]) for total in categories_dict.values()]
            total = sum(amounts)
            
            if total > 0:
                # Filtra le categorie che rappresentano meno dell'1% del totale
                threshold = total * 0.01
                significant_indices = [i for i, amount in enumerate(amounts) if amount > threshold]
                significant_categories = [categories[i] for i in significant_indices]
                significant_amounts = [amounts[i] for i in significant_indices]
                
                colors = plt.cm.Set3(np.linspace(0, 1, len(significant_categories)))
                wedges, texts, autotexts = plt.pie(significant_amounts, 
                                              labels=significant_categories,
                                              colors=colors,
                                              autopct='%1.1f%%',
                                              pctdistance=0.85)
                
                plt.setp(autotexts, size=8, weight="bold")
                plt.setp(texts, size=10)
                
                plt.title(f'🏷️ Distribuzione per Categoria ({currency})', pad=20, fontsize=14)
                plt.tight_layout()
                
                buf = io.BytesIO()
                plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
                buf.seek(0)
                image_buffers.append(buf)
                plt.close()

        # 3. Grafico a barre orizzontali per wallet
        if wallet_totals:
            plt.figure(figsize=(12, 6))
            # Converti le tuple in stringhe per i wallet
            wallets_dict = {}
            for (period, wal), (amount, count) in wallet_totals.items():
                if wal not in wallets_dict:
                    wallets_dict[wal] = [0, 0]
                wallets_dict[wal][0] += float(amount)
                wallets_dict[wal][1] += count

            wallets = list(wallets_dict.keys())
            amounts = [total[0] for total in wallets_dict.values()]
            
            bars = plt.barh(wallets, amounts)
            plt.title(f'💼 Saldo per Portafoglio ({currency})', pad=20, fontsize=14)
            plt.xlabel(f'Importo ({currency})', fontsize=12)
            
            for i, bar in enumerate(bars):
                width = bar.get_width()
                plt.text(width, bar.get_y() + bar.get_height()/2,
                        f'{amounts[i]:,.0f} {currency}',
                        ha='left', va='center', fontsize=10)
            
            plt.grid(True, linestyle='--', alpha=0.7, axis='x')
            plt.gca().xaxis.set_major_formatter(FuncFormatter(currency_formatter))
            
            plt.tight_layout()
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
            buf.seek(0)
            image_buffers.append(buf)
            plt.close()
        
        return image_buffers

    except Exception as e:
        plt.close('all')  # Chiudi tutte le figure in caso di errore
        raise e  # Rilancia l'eccezione per gestirla nel chiamante

@bot.message_handler(commands=["test"])
def create_test_data(message):
    """Crea dati di test con transazioni solo in Euro"""
    
    categories = ["Alimentari", "Trasporti", "Shopping", "Stipendio", "Investimenti", "Utenze", "Svago"]
    descriptions = {
        "Alimentari": ["Spesa settimanale", "Pranzo fuori", "Cena ristorante", "Caffè bar"],
        "Trasporti": ["Benzina", "Biglietto treno", "Taxi", "Manutenzione auto"],
        "Shopping": ["Vestiti", "Elettronica", "Libri", "Accessori"],
        "Stipendio": ["Stipendio mensile", "Bonus", "Freelance", "Consulenza"],
        "Investimenti": ["Azioni", "ETF", "Fondi", "Trading"],
        "Utenze": ["Bolletta luce", "Gas", "Internet", "Telefono"],
        "Svago": ["Cinema", "Palestra", "Concerti", "Videogiochi"]
    }
    locations = ["Milano", "Roma", "Napoli", "Torino", "Online", "Amazon", "Supermercato"]
    
    try:
        session = SessionLocal()
        user = get_or_create_user(session, str(message.from_user.id),
                                message.from_user.username or message.from_user.first_name)
        
        # Crea solo il wallet EUR
        wallet_eur = create_wallet(session, "Principale EUR", "EUR")
        
        # Data di partenza (30 giorni fa)
        start_date = datetime.datetime.now() - timedelta(days=30)
        
        bot.reply_to(message, "🔄 Generazione transazioni di test in corso...")
        
        # Genera 50 transazioni random
        for _ in range(50):
            # Scegli categoria e descrizione correlata
            category = random.choice(categories)
            description = random.choice(descriptions[category])
            
            # Genera importo in base alla categoria
            if category in ["Stipendio", "Investimenti"]:
                amount = random.uniform(500, 3000)  # Importi positivi più alti
            else:
                amount = random.uniform(-500, 100)  # Mix di spese e piccole entrate
            
            # Data random negli ultimi 30 giorni
            random_days = random.randint(0, 30)
            date = start_date + timedelta(days=random_days)
            
            # Crea la transazione
            create_expense(
                session,
                user,
                wallet_eur,  # Usa solo il wallet EUR
                amount,
                description,
                random.choice(locations),
                date,
                category
            )
        
        # Analisi delle transazioni
        transactions = session.query(Expense).join(Wallet).filter(
            Expense.user_id == user.id,
            Wallet.currency == "EUR"
        ).all()
        
        # Calcola il totale
        total = sum(t.amount for t in transactions)
        
        # Crea grafico delle transazioni
        plt.figure(figsize=(10, 6))
        plt.bar(['EUR'], [total], color='#2ecc71')
        plt.title('💰 Saldo Totale', pad=20, fontsize=14)
        plt.ylabel('Saldo (€)')
        
        # Aggiungi etichetta sulla barra
        plt.text(0, total, f'{total:,.2f} €',
                ha='center', va='bottom')
        
        plt.grid(True, linestyle='--', alpha=0.7, axis='y')
        
        # Salva il grafico
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
        buf.seek(0)
        plt.close()
        
        # Invia il riepilogo
        summary = (
            "✅ *Generazione dati di test completata*\n\n"
            f"📊 *Riepilogo:*\n"
            f"• Transazioni: {len(transactions)}\n"
            f"• Saldo EUR: {total:,.2f} €\n\n"
            f"📈 *Categorie utilizzate:*\n"
            f"{', '.join(categories)}\n\n"
            "🔍 Usa il comando /report per vedere l'analisi completa"
        )
        
        bot.send_message(message.chat.id, summary, parse_mode="Markdown")
        bot.send_photo(message.chat.id, buf)
        buf.close()
        
        session.close()
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore durante la generazione dei dati di test: {str(e)}")
        if 'session' in locals():
            session.close()

@bot.message_handler(commands=["reset_db"])
def reset_database(message):
    """Elimina il database e lo ricrea da zero"""
    try:
        # Verifica che l'utente sia autorizzato (potresti voler limitare questo comando)
        session = SessionLocal()
        user = get_or_create_user(session, str(message.from_user.id),
                                message.from_user.username or message.from_user.first_name)
        session.close()

        # Elimina il database
        Base.metadata.drop_all(bind=engine)
        # Ricrea il database
        Base.metadata.create_all(bind=engine)
        
        bot.reply_to(message, "✅ Database eliminato e ricreato con successo!\n\n"
                    "Puoi importare i dati da un CSV usando il comando /import_csv")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore durante il reset del database: {str(e)}")

@bot.message_handler(commands=["import_csv"])
def request_csv_import(message):
    """Richiede il file CSV da importare"""
    bot.reply_to(message, 
                "📤 Inviami il file CSV con le transazioni.\n\n"
                "Il CSV deve avere le seguenti colonne:\n"
                "- amount: importo (numerico)\n"
                "- description: descrizione\n"
                "- category: categoria\n"
                "- date: data (YYYY-MM-DD)\n"
                "- location: luogo\n"
                "- currency: valuta (EUR o SAT)\n\n"
                "La prima riga deve contenere i nomi delle colonne.")
    bot.register_next_step_handler(message, process_csv_import)

def process_csv_import(message):
    """Processa il file CSV inviato"""
    try:
        # Verifica che sia stato inviato un file
        if not message.document:
            bot.reply_to(message, "⚠️ Per favore, invia un file CSV.")
            return
        
        # Verifica che sia un file CSV
        if not message.document.file_name.endswith('.csv'):
            bot.reply_to(message, "⚠️ Il file deve essere in formato CSV.")
            return

        # Scarica il file
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Leggi il CSV
        csv_data = pd.read_csv(io.StringIO(downloaded_file.decode('utf-8')))
        
        # Verifica le colonne necessarie
        required_columns = ['amount', 'description', 'category', 'date', 'location', 'currency']
        missing_columns = [col for col in required_columns if col not in csv_data.columns]
        if missing_columns:
            bot.reply_to(message, f"⚠️ Colonne mancanti nel CSV: {', '.join(missing_columns)}")
            return

        # Inizia l'importazione
        bot.reply_to(message, "🔄 Importazione in corso...")
        
        session = SessionLocal()
        user = get_or_create_user(session, str(message.from_user.id),
                                message.from_user.username or message.from_user.first_name)
        
        wallets = {}  # Cache per i wallet
        imported_count = 0
        errors = []

        for _, row in csv_data.iterrows():
            try:
                # Ottieni o crea il wallet per la valuta
                currency = row['currency'].upper()
                if currency not in wallets:
                    wallets[currency] = create_wallet(session, f"Principale {currency}", currency)
                
                # Crea la transazione
                create_expense(
                    session,
                    user,
                    wallets[currency],
                    float(row['amount']),
                    str(row['description']),
                    str(row['location']),
                    pd.to_datetime(row['date']).to_pydatetime(),
                    str(row['category'])
                )
                imported_count += 1
            except Exception as e:
                errors.append(f"Riga {_+2}: {str(e)}")

        session.close()

        # Invia il report dell'importazione
        report = f"✅ Importazione completata!\n\n" \
                 f"📊 Statistiche:\n" \
                 f"• Transazioni importate: {imported_count}\n" \
                 f"• Valute: {', '.join(wallets.keys())}\n"
        
        if errors:
            report += f"\n⚠️ Errori ({len(errors)}):\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                report += f"\n... e altri {len(errors) - 10} errori"

        bot.reply_to(message, report)

    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore durante l'importazione: {str(e)}")
        if 'session' in locals():
            session.close()

@bot.callback_query_handler(func=lambda call: call.data.startswith(("list_transactions", "edit_tx_", "delete_tx_")))
def list_transactions_callback(call: CallbackQuery):
    try:
        # Estrai l'offset dalla callback data (default 0)
        if call.data == "list_transactions":
            offset = 0
        elif call.data.startswith("list_transactions_"):
            offset = int(call.data.split("_")[-1])
        elif call.data.startswith(("edit_tx_", "delete_tx_")):
            tx_id = int(call.data.split("_")[-1])
            if call.data.startswith("edit_tx_"):
                # TODO: Implementa la logica di modifica
                bot.answer_callback_query(call.id, "Funzionalità di modifica in sviluppo")
                return
            elif call.data.startswith("delete_tx_"):
                # TODO: Implementa la logica di eliminazione
                bot.answer_callback_query(call.id, "Funzionalità di eliminazione in sviluppo")
                return
            return

        session = SessionLocal()
        user = get_or_create_user(session, str(call.from_user.id),
                                call.from_user.username or call.from_user.first_name)
        
        # Recupera sia le transazioni dell'utente che quelle condivise con lui
        own_transactions = session.query(Expense).join(Wallet).filter(
            Expense.user_id == user.id
        )
        
        # Recupera gli ID degli utenti che hanno condiviso con l'utente corrente
        shared_with_me = session.query(SharedAccess.owner_id).filter(
            SharedAccess.viewer_id == user.id
        ).all()
        shared_user_ids = [owner_id for (owner_id,) in shared_with_me]
        
        # Se ci sono condivisioni, aggiungi anche le transazioni condivise
        if shared_user_ids:
            shared_transactions = session.query(Expense).join(Wallet).filter(
                Expense.user_id.in_(shared_user_ids)
            )
            # Unisci le query
            all_transactions = own_transactions.union(shared_transactions)
        else:
            all_transactions = own_transactions
        
        # Ordina e applica offset e limit
        transactions = all_transactions.order_by(
            Expense.date.desc()
        ).offset(offset).limit(6).all()
        
        if not transactions:
            msg_text = "Nessuna transazione trovata."
            if call.message.text != msg_text:
                bot.edit_message_text(msg_text, call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id)
            return
        
        # Usa solo le prime 5 transazioni per la visualizzazione
        display_transactions = transactions[:5]
        has_next = len(transactions) > 5
        has_prev = offset > 0
        
        # Costruisci il messaggio con le transazioni
        msg_text = f"*📋 Transazioni {offset+1}-{offset+len(display_transactions)}:*\n\n"
        
        for tx in display_transactions:
            # Recupera il proprietario della transazione
            owner = session.query(User).get(tx.user_id)
            is_own = owner.id == user.id
            
            # Arrotonda l'importo a 2 decimali e formatta con segno e valuta
            rounded_amount = round(tx.amount, 2)
            amount = f"{rounded_amount:+,.2f} {tx.wallet.currency}"
            # Emoji in base al segno dell'importo
            emoji = "💰" if rounded_amount >= 0 else "💸"
            
            # Aggiungi indicatore del proprietario
            owner_indicator = "📱" if is_own else f"👤 {owner.username or owner.telegram_id}"
            
            msg_text += (
                f"{emoji} *{tx.date.strftime('%d/%m/%Y')}* {owner_indicator}\n"
                f"└ Importo: {amount}\n"
                f"└ Categoria: {tx.category}\n"
                f"└ Descrizione: {tx.description}\n"
                f"└ Luogo: {tx.location}\n"
                f"└ Wallet: {tx.wallet.name}\n\n"
            )
        
        # Crea la tastiera inline con i bottoni di navigazione e azione
        markup = InlineKeyboardMarkup()
        row = []
        
        # Bottoni di navigazione
        if has_prev:
            row.append(InlineKeyboardButton("⬅️ Precedenti", 
                      callback_data=f"list_transactions_{offset-5}"))
        if has_next:
            row.append(InlineKeyboardButton("Successive ➡️", 
                      callback_data=f"list_transactions_{offset+5}"))
        if row:
            markup.row(*row)
        
        # Aggiungi bottoni di azione solo per le proprie transazioni
        for tx in display_transactions:
            if tx.user_id == user.id:  # Solo per le proprie transazioni
                desc = tx.description[:15] + "..." if len(tx.description) > 15 else tx.description
                markup.row(
                    InlineKeyboardButton(f"✏️ {desc}", 
                                       callback_data=f"edit_tx_{tx.id}"),
                    InlineKeyboardButton(f"🗑️ {desc}", 
                                       callback_data=f"delete_tx_{tx.id}")
                )
        
        # Aggiungi un bottone per il report completo
        markup.row(InlineKeyboardButton("📊 Report Completo", callback_data="show_report"))
        
        if call.data == "list_transactions":
            bot.send_message(call.message.chat.id, msg_text, 
                           parse_mode="Markdown", 
                           reply_markup=markup)
        else:
            # Altrimenti aggiorna il messaggio esistente
            bot.edit_message_text(msg_text, 
                                call.message.chat.id, 
                                call.message.message_id,
                                parse_mode="Markdown", 
                                reply_markup=markup)
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"⚠️ Errore nel recupero delle transazioni: {str(e)}")
    finally:
        if 'session' in locals():
            session.close()

@bot.message_handler(commands=["share"])
def share_access(message):
    """Condividi l'accesso alle tue transazioni con un altro utente"""
    bot.reply_to(message, 
                "🔗 Per condividere le tue transazioni con un altro utente, "
                "invia il suo username Telegram (es: @username) o ID.")
    bot.register_next_step_handler(message, process_share_access)

def process_share_access(message):
    try:
        session = SessionLocal()
        sharer = get_or_create_user(session, str(message.from_user.id),
                                  message.from_user.username or message.from_user.first_name)
        
        # Estrai username o ID
        target = message.text.strip()
        if target.startswith('@'):
            target = target[1:]  # Rimuovi @
            target_user = session.query(User).filter(User.username == target).first()
        else:
            # Assume sia un ID
            target_user = session.query(User).filter(User.telegram_id == target).first()
        
        if not target_user:
            bot.reply_to(message, "⚠️ Utente non trovato. Assicurati che abbia già utilizzato il bot.")
            return
        
        if target_user.id == sharer.id:
            bot.reply_to(message, "⚠️ Non puoi condividere le transazioni con te stesso.")
            return
        
        # Verifica se la condivisione esiste già
        existing_share = session.query(SharedAccess).filter(
            SharedAccess.owner_id == sharer.id,
            SharedAccess.viewer_id == target_user.id
        ).first()
        
        if existing_share:
            bot.reply_to(message, f"⚠️ Hai già condiviso le tue transazioni con {target_user.username or target_user.telegram_id}")
            return
        
        # Crea la condivisione
        new_share = SharedAccess(
            owner_id=sharer.id,
            viewer_id=target_user.id
        )
        session.add(new_share)
        session.commit()
        
        # Notifica entrambi gli utenti
        bot.reply_to(message, 
                    f"✅ Hai condiviso le tue transazioni con {target_user.username or target_user.telegram_id}")
        
        bot.send_message(target_user.telegram_id,
                        f"🔔 {sharer.username or sharer.telegram_id} ha condiviso le sue transazioni con te.\n"
                        "Usa /shared_reports per vedere i report condivisi.")
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore durante la condivisione: {str(e)}")
    finally:
        session.close()

@bot.callback_query_handler(func=lambda call: call.data.startswith("unshare_"))
def unshare_callback(call: CallbackQuery):
    """Gestisce la revoca dell'accesso"""
    try:
        viewer_id = int(call.data.split("_")[1])
        
        session = SessionLocal()
        owner = get_or_create_user(session, str(call.from_user.id),
                                 call.from_user.username or call.from_user.first_name)
        
        # Elimina la condivisione
        share = session.query(SharedAccess).filter(
            SharedAccess.owner_id == owner.id,
            SharedAccess.viewer_id == viewer_id
        ).first()
        
        if share:
            viewer = session.query(User).get(viewer_id)
            session.delete(share)
            session.commit()
            
            bot.answer_callback_query(call.id, "✅ Accesso revocato con successo!")
            bot.edit_message_text(
                f"✅ Accesso revocato per {viewer.username or viewer.telegram_id}",
                call.message.chat.id,
                call.message.message_id
            )
            
            # Notifica l'utente che ha perso l'accesso
            bot.send_message(viewer.telegram_id,
                           f"ℹ️ {owner.username or owner.telegram_id} ha revocato la condivisione delle transazioni.")
        else:
            bot.answer_callback_query(call.id, "⚠️ Condivisione non trovata")
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")
    finally:
        session.close()

@bot.message_handler(commands=["shared_reports"])
def list_shared_reports(message):
    """Mostra la lista degli utenti che hanno condiviso le loro transazioni con te"""
    try:
        session = SessionLocal()
        user = get_or_create_user(session, str(message.from_user.id),
                                message.from_user.username or message.from_user.first_name)
        
        # Recupera tutte le condivisioni dove l'utente è viewer
        shares = session.query(SharedAccess).join(
            User, SharedAccess.owner_id == User.id
        ).filter(
            SharedAccess.viewer_id == user.id
        ).all()
        
        if not shares:
            bot.reply_to(message, "Nessuno ha condiviso le proprie transazioni con te.")
            return
        
        # Crea tastiera inline con bottoni per vedere i report
        markup = InlineKeyboardMarkup()
        for share in shares:
            owner = session.query(User).get(share.owner_id)
            markup.row(InlineKeyboardButton(
                f"📊 Report di {owner.username or owner.telegram_id}",
                callback_data=f"shared_report_{owner.id}"
            ))
        
        bot.reply_to(message, 
                    "👥 Seleziona un utente per vedere il suo report:",
                    reply_markup=markup)
        
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore: {str(e)}")
    finally:
        session.close()

@bot.callback_query_handler(func=lambda call: call.data.startswith("shared_report_"))
def shared_report_callback(call: CallbackQuery):
    """Gestisce la visualizzazione del report di un altro utente"""
    try:
        owner_id = int(call.data.split("_")[2])
        
        session = SessionLocal()
        viewer = get_or_create_user(session, str(call.from_user.id),
                                  call.from_user.username or call.from_user.first_name)
        
        # Verifica che l'accesso sia ancora valido
        share = session.query(SharedAccess).filter(
            SharedAccess.owner_id == owner_id,
            SharedAccess.viewer_id == viewer.id
        ).first()
        
        if not share:
            bot.answer_callback_query(call.id, "⚠️ Non hai più accesso a questo report")
            return
        
        owner = session.query(User).get(owner_id)
        
        # Modifica il messaggio per mostrare che stiamo generando il report
        bot.edit_message_text(
            f"🔄 Generando il report per {owner.username or owner.telegram_id}...",
            call.message.chat.id,
            call.message.message_id
        )
        
        # Qui chiama la tua funzione di report esistente ma passa l'owner_id opzionale
        # Dovrai modificare la funzione process_report per accettare un parametro owner_id opzionale
        process_report(call.message, owner_id=owner_id)
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")
    finally:
        session.close()

@bot.message_handler(func=lambda message: message.text == "👥 Condivisioni")
def share_menu_message(message):
    """Gestisce l'apertura del menu condivisioni da keyboard button"""
    try:
        username = get_username_from_message(message)
        show_share_menu(message.chat.id, username=username)
    except Exception as e:
        bot.reply_to(message, f"⚠️ Errore: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == "share_menu")
def share_menu_callback(call: CallbackQuery):
    """Gestisce l'apertura del menu condivisioni da callback"""
    try:
        bot.answer_callback_query(call.id)
        username = get_username_from_callback(call)
        show_share_menu(call.message.chat.id, call.message.message_id, username)
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")

def show_share_menu(chat_id, message_id=None, username=None):
    """Mostra il menu di condivisione"""
    try:
        session = SessionLocal()
        user = get_or_create_user(session, str(chat_id), username or str(chat_id))
        
        # Recupera le condivisioni attive
        shares = session.query(SharedAccess).join(
            User, SharedAccess.viewer_id == User.id
        ).filter(
            SharedAccess.owner_id == user.id
        ).all()
        
        # Crea il messaggio con la lista delle condivisioni attive
        msg_text = "*👥 Menu Condivisione*\n\n"
        if shares:
            msg_text += "*Condivisioni attive:*\n"
            for share in shares:
                viewer = session.query(User).get(share.viewer_id)
                msg_text += f"• {viewer.username or viewer.telegram_id}\n"
        else:
            msg_text += "_Nessuna condivisione attiva_\n"
        
        # Crea la tastiera con le opzioni
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("➕ Nuova Condivisione", callback_data="new_share"),
            InlineKeyboardButton("❌ Revoca Accesso", callback_data="manage_shares")
        )
        markup.row(
            InlineKeyboardButton("👀 Report Condivisi", callback_data="view_shared"),
            InlineKeyboardButton("🔙 Menu Principale", callback_data="main_menu")
        )
        
        if message_id:
            # Aggiorna il messaggio esistente
            bot.edit_message_text(
                msg_text,
                chat_id,
                message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
        else:
            # Invia un nuovo messaggio
            bot.send_message(
                chat_id,
                msg_text,
                parse_mode="Markdown",
                reply_markup=markup
            )
        
    except Exception as e:
        error_msg = f"⚠️ Errore nel menu condivisione: {str(e)}"
        if message_id:
            bot.edit_message_text(error_msg, chat_id, message_id)
        else:
            bot.send_message(chat_id, error_msg)
    finally:
        if 'session' in locals():
            session.close()

@bot.callback_query_handler(func=lambda call: call.data == "new_share")
def new_share_callback(call: CallbackQuery):
    """Avvia il processo di nuova condivisione"""
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "🔗 Per condividere le tue transazioni con un altro utente,\n"
        "invia il suo username Telegram (es: @username) o ID.\n\n"
        "_Rispondi direttamente a questo messaggio._",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(call.message, process_share_access)

@bot.callback_query_handler(func=lambda call: call.data == "manage_shares")
def manage_shares_callback(call: CallbackQuery):
    """Gestisce la revoca delle condivisioni"""
    try:
        session = SessionLocal()
        user = get_or_create_user(session, str(call.from_user.id),
                                call.from_user.username or call.from_user.first_name)
        
        # Recupera le condivisioni attive
        shares = session.query(SharedAccess).join(
            User, SharedAccess.viewer_id == User.id
        ).filter(
            SharedAccess.owner_id == user.id
        ).all()
        
        if not shares:
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Torna al Menu", callback_data="share_menu"))
            bot.edit_message_text(
                "Non hai condivisioni attive.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            bot.answer_callback_query(call.id)
            return
        
        # Crea tastiera inline con bottoni per revocare l'accesso
        markup = InlineKeyboardMarkup()
        for share in shares:
            viewer = session.query(User).get(share.viewer_id)
            markup.row(InlineKeyboardButton(
                f"❌ Revoca {viewer.username or viewer.telegram_id}",
                callback_data=f"unshare_{viewer.id}"
            ))
        markup.row(InlineKeyboardButton("🔙 Torna al Menu", callback_data="share_menu"))
        
        bot.edit_message_text(
            "Seleziona un utente per revocare l'accesso:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")
    finally:
        session.close()

@bot.callback_query_handler(func=lambda call: call.data == "view_shared")
def view_shared_callback(call: CallbackQuery):
    """Mostra i report condivisi con l'utente"""
    try:
        session = SessionLocal()
        user = get_or_create_user(session, str(call.from_user.id),
                                call.from_user.username or call.from_user.first_name)
        
        # Recupera le condivisioni dove l'utente è viewer
        shares = session.query(SharedAccess).join(
            User, SharedAccess.owner_id == User.id
        ).filter(
            SharedAccess.viewer_id == user.id
        ).all()
        
        if not shares:
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Torna al Menu", callback_data="share_menu"))
            bot.edit_message_text(
                "Nessuno ha condiviso le proprie transazioni con te.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            bot.answer_callback_query(call.id)
            return
        
        # Crea tastiera inline con bottoni per vedere i report
        markup = InlineKeyboardMarkup()
        for share in shares:
            owner = session.query(User).get(share.owner_id)
            markup.row(InlineKeyboardButton(
                f"📊 Report di {owner.username or owner.telegram_id}",
                callback_data=f"shared_report_{owner.id}"
            ))
        markup.row(InlineKeyboardButton("🔙 Torna al Menu", callback_data="share_menu"))
        
        bot.edit_message_text(
            "👥 Seleziona un utente per vedere il suo report:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"⚠️ Errore: {str(e)}")
    finally:
        session.close()
