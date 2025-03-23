from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL

import datetime
from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Enum, Boolean
from sqlalchemy.orm import relationship
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True)
    username = Column(String)
    expenses = relationship("Expense", back_populates="user")
    shared_expenses = relationship("SharedExpense", back_populates="shared_with")

class Wallet(Base):
    __tablename__ = "wallets"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)
    currency = Column(Enum("EUR", "BTC", "SAT", name="currency_types"), default="EUR")
    expenses = relationship("Expense", back_populates="wallet")

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    wallet_id = Column(Integer, ForeignKey("wallets.id"))
    amount = Column(Float)  # Importo della spesa/entrata
    description = Column(String)
    location = Column(String)
    date = Column(DateTime, default=datetime.datetime.utcnow)
    category = Column(String)  # Es. "alimentari", "trasporti", ecc.
    user = relationship("User", back_populates="expenses")
    wallet = relationship("Wallet", back_populates="expenses")
    shared = relationship("SharedExpense", back_populates="expense")

class SharedExpense(Base):
    __tablename__ = "shared_expenses"
    id = Column(Integer, primary_key=True, index=True)
    expense_id = Column(Integer, ForeignKey("expenses.id"))
    shared_with_id = Column(Integer, ForeignKey("users.id"))  # Utente con cui condividere
    can_view = Column(Boolean, default=True)
    expense = relationship("Expense", back_populates="shared")
    shared_with = relationship("User", back_populates="shared_expenses")

class SharedAccess(Base):
    __tablename__ = "shared_access"
    
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    viewer_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    owner = relationship("User", foreign_keys=[owner_id])
    viewer = relationship("User", foreign_keys=[viewer_id])
