# handlers/filters.py
from telegram.ext import filters
from telegram import Message
import database

class AdminFilter(filters.BaseFilter):
    """Custom filter to check if a user is an admin."""
    def filter(self, message: Message) -> bool:
        # This filter works for both standard messages and callback queries,
        # as both have a `from_user` attribute.
        if not message.from_user:
            return False
        return database.is_admin(message.from_user.id)

# Create a single instance of the filter to be used across the application
admin_filter = AdminFilter()