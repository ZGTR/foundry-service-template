"""Seed SYNTHETIC Alphazed-shaped data: a subject catalogue and two schools of learners.

    DATABASE_URL=postgresql+psycopg://... python scripts/seed_alphazed.py

Deterministic (fixed seed), idempotent (truncates the `az_*` tables first). Names and
emails are invented; nothing here comes from production.
"""

from __future__ import annotations

import os
import random

from sqlalchemy import create_engine, text

SUBJECTS = [
    ("arabic-letters", "Arabic Letters", "الحروف العربية", "3-4"),
    ("counting", "Counting 1-10", "العد من ١ إلى ١٠", "3-4"),
    ("animals", "Animals Around Us", "الحيوانات من حولنا", "5-6"),
    ("arabic-words", "First Arabic Words", "كلماتي العربية الأولى", "5-6"),
    ("addition", "Adding Numbers", "جمع الأعداد", "7-8"),
    ("reading", "Reading Short Stories", "قراءة قصص قصيرة", "7-8"),
]
SCHOOLS = {
    "school-amal": ["Lina", "Omar", "Yousef", "Sara", "Adam", "Maryam"],
    "school-noor": ["Huda", "Karim", "Zaid", "Rania"],
}


def main() -> None:
    url = os.environ["DATABASE_URL"]
    rng = random.Random(20261105)
    with create_engine(url).begin() as conn:
        conn.execute(
            text(
                "TRUNCATE az_progress, az_learners, az_content_bytes, az_subjects RESTART IDENTITY CASCADE"
            )
        )
        byte_ids: dict[str, list[int]] = {}
        for slug, en, ar, age in SUBJECTS:
            sid = conn.execute(
                text(
                    "INSERT INTO az_subjects (slug, title_en, title_ar, age_group) VALUES (:s,:e,:a,:g) RETURNING id"
                ),
                {"s": slug, "e": en, "a": ar, "g": age},
            ).scalar_one()
            byte_ids[slug] = [
                conn.execute(
                    text(
                        "INSERT INTO az_content_bytes (subject_id, title, bit_count) VALUES (:s,:t,:n) RETURNING id"
                    ),
                    {"s": sid, "t": f"{en} — byte {i}", "n": rng.randint(4, 9)},
                ).scalar_one()
                for i in range(1, 5)
            ]
        for org, names in SCHOOLS.items():
            for name in names:
                age = rng.choice(["3-4", "5-6", "7-8"])
                lid = conn.execute(
                    text(
                        "INSERT INTO az_learners (org_id, display_name, age_group, parent_email) "
                        "VALUES (:o,:n,:g,:e) RETURNING id"
                    ),
                    {
                        "o": org,
                        "n": name,
                        "g": age,
                        "e": f"parent.of.{name.lower()}@example.invalid",
                    },
                ).scalar_one()
                skill = rng.randint(35, 95)
                for slug in rng.sample(list(byte_ids), 3):
                    for bid in byte_ids[slug][: rng.randint(2, 4)]:
                        score = max(0, min(100, skill + rng.randint(-15, 15)))
                        conn.execute(
                            text("INSERT INTO az_progress VALUES (:l,:b,:o,:s,:c)"),
                            {"l": lid, "b": bid, "o": org, "s": score, "c": score >= 50},
                        )
    print(
        "seeded",
        len(SUBJECTS),
        "subjects,",
        sum(map(len, SCHOOLS.values())),
        "learners in",
        list(SCHOOLS),
    )


if __name__ == "__main__":
    main()
