from sqlalchemy import Column, String, Integer, Boolean, DateTime
from datetime import datetime
from database import Base


class UserQuota(Base):
    """Tracks user generation quota and premium status"""
    __tablename__ = "user_quotas"

    user_id = Column(String, primary_key=True, index=True)
    generation_count = Column(Integer, default=0)
    is_premium = Column(Boolean, default=False)
    premium_since = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
