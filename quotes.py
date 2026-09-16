"""
quotes.py - A curated grab-bag of short motivational lines from nerdy
movies, shows, and games, plus punchy congratulations messages for
finishing tasks and pomodoro sessions. Purely cosmetic flavor text for the
toast celebrations (see toast.py, pomodoro_tab.py, checklist_tab.py) and
the Progress tab's quote card, which draws a fresh one each time that tab
is opened.
"""
import random

# (quote, source) pairs, kept short and as close to the on-screen/on-page
# line as memory allows.
QUOTES = [
    ("Do. Or do not. There is no try.", "Yoda, Star Wars"),
    ("The Force will be with you. Always.", "Obi-Wan Kenobi, Star Wars"),
    ("Your focus determines your reality.", "Qui-Gon Jinn, Star Wars: The Phantom Menace"),
    ("Not all those who wander are lost.", "J.R.R. Tolkien, The Lord of the Rings"),
    ("All we have to decide is what to do with the time that is given us.", "Gandalf, The Fellowship of the Ring"),
    ("There's some good in this world, and it's worth fighting for.", "Samwise Gamgee, The Two Towers"),
    ("Even the smallest person can change the course of the future.", "Galadriel, The Fellowship of the Ring"),
    ("It is our choices that show what we truly are, far more than our abilities.", "Albus Dumbledore, Harry Potter"),
    ("Happiness can be found even in the darkest of times, if one only remembers to turn on the light.", "Albus Dumbledore, Harry Potter"),
    ("It does not do to dwell on dreams and forget to live.", "Albus Dumbledore, Harry Potter"),
    ("The needs of the many outweigh the needs of the few.", "Spock, Star Trek II: The Wrath of Khan"),
    ("Things are only impossible until they're not.", "Jean-Luc Picard, Star Trek: The Next Generation"),
    ("Live long and prosper.", "Spock, Star Trek"),
    ("With great power comes great responsibility.", "Spider-Man"),
    ("I can do this all day.", "Steve Rogers, Captain America"),
    ("Whatever it takes.", "Tony Stark, Avengers: Endgame"),
    ("We never lose our demons. We only learn to live above them.", "Doctor Strange"),
    ("Why do we fall? So we can learn to pick ourselves back up.", "Alfred Pennyworth, Batman Begins"),
    ("It's not who I am underneath, but what I do that defines me.", "Batman Begins"),
    ("Sucking at something is the first step to becoming sorta good at something.", "Jake the Dog, Adventure Time"),
    ("Destiny is a funny thing. You never know how things are going to work out.", "Uncle Iroh, Avatar: The Last Airbender"),
    ("Pain and suffering are relative matters.", "Uncle Iroh, Avatar: The Last Airbender"),
    ("Believe it!", "Naruto Uzumaki, Naruto"),
    ("A person grows up when he's able to overcome hardships.", "Jiraiya, Naruto"),
    ("Plus Ultra!", "My Hero Academia"),
    ("The game is afoot.", "Sherlock Holmes"),
    ("We're all stories in the end. Just make it a good one.", "The Doctor, Doctor Who"),
    ("Don't Panic.", "The Hitchhiker's Guide to the Galaxy"),
    ("Free your mind.", "Morpheus, The Matrix"),
    ("It's dangerous to go alone! Take this.", "The Legend of Zelda"),
    ("Despite everything, it's still you.", "Undertale"),
    ("I aim to misbehave.", "Malcolm Reynolds, Firefly"),
    ("Fear is the mind-killer.", "Litany Against Fear, Dune"),
    ("Home is now behind you, the world is ahead.", "The Hobbit"),
]

# Short, punchy headlines for finishing a checklist task.
TASK_CONGRATS = [
    "Achievement unlocked!",
    "Critical hit!",
    "Nice work!",
    "Quest item acquired!",
    "+1 to Productivity!",
    "Side quest complete!",
    "Nailed it!",
    "You rolled a nat 20!",
    "Task obliterated!",
    "XP gained!",
    "Way to go!",
    "Loot secured!",
]

# Short, punchy headlines for finishing a pomodoro work session.
POMODORO_CONGRATS = [
    "Focus session complete!",
    "Boss round defeated!",
    "Deep work achieved!",
    "Session in the books!",
    "Another one down!",
    "Focus level: expert!",
    "You held the line!",
    "Combo extended!",
]


def random_quote():
    return random.choice(QUOTES)


def random_task_congrats():
    return random.choice(TASK_CONGRATS)


def random_pomodoro_congrats():
    return random.choice(POMODORO_CONGRATS)
