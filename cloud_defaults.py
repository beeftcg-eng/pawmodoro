"""
cloud_defaults.py - The shared Pawmodoro Supabase project that the desktop
app, phone app and Deckbuilder all connect to out of the box, so a friend
only has to enter an email and password.

The anon key is *meant* to ship inside client apps (it has the `anon` role
and nothing more); what protects each person's data is the row-level
security in supabase/schema.sql, not the secrecy of this key. Never put a
`service_role` key here. Keep in sync with docs/app.js and Deckbuilder's
src/shared/pawmodoroDefaults.ts.
"""

DEFAULT_URL = "https://cinxclbsgamdprftcbek.supabase.co"
DEFAULT_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNpbnhjbGJzZ2FtZHByZnRjYmVrIiwicm9sZSI6ImFub24i"
    "LCJpYXQiOjE3ODk1NTc4OTIsImV4cCI6MjEwNTEzMzg5Mn0.1hpyBV2ZO3MeQUSnx3K5-XG6BbzY-gXb-4uG6Ls7Fr0"
)
