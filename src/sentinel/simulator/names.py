"""Name lists for synthetic people and businesses.

Names are common across South Africa's language groups. Business name parts are
generic words, so generated names should not match real brands.
"""

FEMALE_FIRST_NAMES: tuple[str, ...] = (
    "Anele", "Anika", "Ayanda", "Ayesha", "Boitumelo", "Busisiwe", "Chantelle",
    "Charlene", "Dineo", "Elize", "Emma", "Fatima", "Jessica", "Karabo", "Kavitha",
    "Kgomotso", "Lerato", "Lindiwe", "Marelize", "Michelle", "Mpho", "Naledi",
    "Nandi", "Nicole", "Nokuthula", "Nomvula", "Nosipho", "Palesa", "Precious",
    "Priya", "Refilwe", "Sarah", "Shanice", "Sibongile", "Siphokazi", "Tamryn",
    "Thandiwe", "Thembi", "Zanele", "Zintle",
)  # fmt: skip

MALE_FIRST_NAMES: tuple[str, ...] = (
    "Andile", "Ashwin", "Bongani", "David", "Ebrahim", "Francois", "Jabulani",
    "Jaco", "Jason", "Johan", "Kabelo", "Kagiso", "Katlego", "Kyle", "Lungelo",
    "Luyanda", "Lwazi", "Mandla", "Michael", "Mthunzi", "Musa", "Pieter", "Pravin",
    "Rajesh", "Riaan", "Ruan", "Ryan", "Sipho", "Siyabonga", "Sizwe", "Thabo",
    "Themba", "Tshepo", "Tumelo", "Vusi", "Willem", "Xolani", "Yusuf",
)  # fmt: skip

SURNAMES: tuple[str, ...] = (
    "Adams", "Baloyi", "Botha", "Buthelezi", "Chetty", "Coetzee", "Dlamini",
    "Du Plessis", "Govender", "Hendricks", "Hlongwane", "Ismail", "Jacobs", "Khan",
    "Khumalo", "Mabaso", "Mahlangu", "Maluleke", "Mathebula", "Mkhize", "Mokoena",
    "Molefe", "Moodley", "Mthembu", "Mudau", "Naidoo", "Ndlovu", "Nel", "Ngcobo",
    "Nkosi", "Ntuli", "Nxumalo", "Patel", "Petersen", "Pretorius", "Radebe",
    "Reddy", "Shabalala", "Sithole", "Smith", "Steyn", "Van der Merwe", "Van Wyk",
    "Venter", "Williams", "Zulu",
)  # fmt: skip

# Business names are built as "<word> <trade> (Pty) Ltd".
BUSINESS_NAME_WORDS: tuple[str, ...] = (
    "Acacia", "Amber", "Baobab", "Bluegum", "Cedar", "Coral", "Crane", "Delta",
    "Eagle", "Falcon", "Fynbos", "Granite", "Harbour", "Highveld", "Indlovu",
    "Ironwood", "Jacaranda", "Karoo", "Kestrel", "Kudu", "Lowveld", "Marula",
    "Meridian", "Oryx", "Protea", "Quartz", "Riverbend", "Silverleaf", "Summit",
    "Umoya",
)  # fmt: skip

BUSINESS_TRADES: tuple[str, ...] = (
    "Auto Repairs", "Catering", "Cleaning Services", "Construction", "Consulting",
    "Electrical", "Engineering", "Farming", "Furniture", "Hardware", "Holdings",
    "Logistics", "Plumbing", "Printing", "Projects", "Property", "Security",
    "Trading", "Transport", "Wholesale",
)  # fmt: skip
