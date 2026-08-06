#!/usr/bin/env python3
"""
Memory subcategories for personalized memory graph
Defines semantic categories for organizing memories
"""

import torch
import numpy as np
from src.utils.perstream_utils import get_text_embedding

MEMORY_SUBCATEGORIES = {
        # Personal Identity
        "name": "names, personal names, identity, labels, written words, signage",
        "identification": "ID numbers, license plates, badges, signs, identification information",
        "vehicle": "vehicles, cars, trucks, transportation objects, automobiles",
        "contact": "phone numbers, email addresses, contact information, personal details",
        "education": "university, school, graduation, educational background, degrees",

        # Basic Profile
        "nickname": "nicknames, aliases, informal names, what people call someone",
        "age": "user age, years old, birthday",
        "physical": "height, weight, physical appearance, body measurements",
        "birth": "birth date, born on, date of birth, birthday",
        "employment": "work, job, employment, career, workplace, company",

        # Health and Medical
        "medical": "medical history, health conditions, diseases, illness, medical records",
        "health": "health status, feeling healthy, medical condition, wellness",

        # Transactions and Assets
        "transactions": "purchases, buying, shopping, transaction records, receipts",
        "financial": "bank accounts, savings, financial information, money, banking",
        "cards": "credit cards, payment cards, wallets, coupons, membership cards",

        # Daily Behavior
        "apps": "mobile apps, app usage, smartphone applications, digital tools",
        "schedule": "calendar, meetings, appointments, schedule, time management",
        "browsing": "search history, web browsing, internet searches, online activity",
        "mentioned": "talking about, discussing, mentioning items, conversations",
        "phrases": "common phrases, catchphrases, frequently said words, expressions",

        # Location Information
        "travel": "commute, transportation, travel routes, getting around",
        "location": "place, current location, where someone is, geographical position",
        "places": "historical locations, places visited, travel history",

        # Social Information
        "relationships": "friends, social connections, relationships, family",
        "birthdays": "others' birthdays, remembering dates, celebrations",
        "interests": "others' interests, preferences, hobbies, likes and dislikes",
        "addresses": "home addresses, locations, residential information",

        # Personal Interests and Preferences
        "personal_interests": "hobbies, preferences, likes, personal interests",
        "notes": "notes, memos, written reminders, personal documentation",

        # In-App Data
        "media": "photos, videos, documents, media files, digital content",
        "app_data": "third-party app data, application information, digital records",
        "contacts_list": "contact lists, phone book, address book, saved contacts",
        "chat": "chat history, messaging, conversations, communication logs",
        "messages": "emails, SMS, text messages, digital communications"
    }

def get_memory_subclass_embeddings(model, processor):
    """Get embeddings for memory subclasses - using mean pooling of prompt embeddings"""

    embeddings = {}
    for category, text in MEMORY_SUBCATEGORIES.items():
        # Use a descriptive prompt without generation
        prompt = f"Category: {category}. Description: {text}"
        embeddings[category] = get_text_embedding(prompt)
        print(f"Processed memory category: {category}")

    # combine as a matrix
    category_names = list(embeddings.keys())
    embeddings_matrix = np.concatenate(list(embeddings.values()), axis=0)
    print(f"Combined embeddings matrix shape: {embeddings_matrix.shape}")
    
    return embeddings_matrix, category_names