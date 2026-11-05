"""Create the two companies' databases in a MySQL 8 server, as each business runs them.

    python e2e/fixtures/seed_company_databases.py mysql+pymysql://root:root@127.0.0.1:33306

* `alqosh` -- Alphazed's learning backend: parents, children (nickname, age group), age
  groups (real L1..L4 labels), subjects (real Arabic titles), chapters, content_bytes,
  content_byte_user (status, top_score), user_streaks, achievements, user_achievements.
* `shepherd` -- Shepherd's control plane: organisations, users, memberships, employees,
  playbook_runs, node_executions (cost_micros, tokens, model_id, cache_state).

Table and column names come from the products' own code and recorded prod fixtures (see
docs/slices/v1-real-use-cases.md). The ROWS are synthetic and deterministic; values such
as model ids, terminal reasons and per-call costs are taken from Shepherd's local-seed runs.

Each database gets a read-only user with SELECT on only the tables its tool pack needs
(`alqosh_reader`, `shepherd_reader`): `parents.email` and `users.email` are unreachable to
the gateway by grant, whatever a statement says.
"""

from __future__ import annotations

import random
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text

NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
RNG = random.Random(20261105)

ALQOSH_DDL = """
CREATE TABLE parents (id CHAR(36) PRIMARY KEY, email VARCHAR(255) UNIQUE NOT NULL,
  app_name VARCHAR(32) NOT NULL, created_at DATETIME NOT NULL);
CREATE TABLE age_groups (id INT PRIMARY KEY, app_name VARCHAR(32), grade VARCHAR(255), text VARCHAR(255));
CREATE TABLE children (id CHAR(36) PRIMARY KEY, parent_id CHAR(36) NOT NULL, nickname VARCHAR(64) NOT NULL,
  gender ENUM('male','female'), birth_date DATE, age_group_id INT, avatar_image_url VARCHAR(512),
  score INT NOT NULL DEFAULT 0, app_name VARCHAR(32) NOT NULL,
  FOREIGN KEY (parent_id) REFERENCES parents(id), FOREIGN KEY (age_group_id) REFERENCES age_groups(id));
CREATE TABLE subjects (id INT PRIMARY KEY, label_text VARCHAR(255) NOT NULL, language VARCHAR(8),
  is_paid BOOLEAN, status VARCHAR(32), visual VARCHAR(64), content_type VARCHAR(32));
CREATE TABLE chapters (id INT PRIMARY KEY, subject_id INT NOT NULL, label_text VARCHAR(255), status VARCHAR(32),
  FOREIGN KEY (subject_id) REFERENCES subjects(id));
CREATE TABLE content_bytes (id INT PRIMARY KEY, chapter_id INT NOT NULL, type VARCHAR(70), `order` INT,
  play_mode VARCHAR(70), label_text VARCHAR(255), FOREIGN KEY (chapter_id) REFERENCES chapters(id));
CREATE TABLE content_byte_user (id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id CHAR(36) NOT NULL,
  content_byte_id INT NOT NULL, status VARCHAR(16) NOT NULL, top_score INT);
CREATE TABLE user_streaks (user_id CHAR(36) PRIMARY KEY, total_streak_days INT, total_coins INT);
CREATE TABLE achievements (id INT PRIMARY KEY, main_text VARCHAR(128), description VARCHAR(255),
  award_coins INT, significance INT);
CREATE TABLE user_achievements (user_id CHAR(36), achievement_id INT, progress INT, status VARCHAR(16),
  PRIMARY KEY (user_id, achievement_id));
"""

SHEPHERD_DDL = """
CREATE TABLE organisations (org_id VARCHAR(64) PRIMARY KEY, display_name VARCHAR(255) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'active', current_graph_version INT NOT NULL DEFAULT 0,
  created_by_user_id VARCHAR(64) NULL, created_at DATETIME NOT NULL);
CREATE TABLE users (user_id VARCHAR(64) PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE,
  display_name VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL);
CREATE TABLE memberships (org_id VARCHAR(64) NOT NULL, user_id VARCHAR(64) NOT NULL, role VARCHAR(32) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE, created_at DATETIME NOT NULL, PRIMARY KEY (org_id, user_id),
  FOREIGN KEY (org_id) REFERENCES organisations(org_id), FOREIGN KEY (user_id) REFERENCES users(user_id));
CREATE TABLE employees (org_id VARCHAR(64) NOT NULL, employee_id VARCHAR(64) NOT NULL, agent_id VARCHAR(64) NULL,
  product_id VARCHAR(64) NULL, role VARCHAR(64) NOT NULL, agent_name VARCHAR(128) NOT NULL,
  routing_profile VARCHAR(128) NULL, autonomy_level INT NOT NULL DEFAULT 0,
  status VARCHAR(32) NOT NULL DEFAULT 'idle', created_at DATETIME NOT NULL,
  PRIMARY KEY (org_id, employee_id), FOREIGN KEY (org_id) REFERENCES organisations(org_id));
CREATE TABLE playbook_runs (org_id VARCHAR(64) NOT NULL, run_id VARCHAR(64) NOT NULL,
  playbook_id VARCHAR(64) NOT NULL, workflow_version INT NOT NULL, employee_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'queued', terminal_reason VARCHAR(64) NULL,
  refused_bound VARCHAR(32) NULL, refused_node_id VARCHAR(64) NULL, max_spend_micros BIGINT NOT NULL,
  max_wall_clock_ms BIGINT NOT NULL, max_tokens INT NOT NULL, max_node_executions INT NOT NULL,
  requested_by VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL, started_at DATETIME NULL,
  ended_at DATETIME NULL, final_output JSON NULL, PRIMARY KEY (org_id, run_id));
CREATE TABLE node_executions (org_id VARCHAR(64) NOT NULL, execution_id VARCHAR(64) NOT NULL,
  run_id VARCHAR(64) NOT NULL, node_id VARCHAR(64) NOT NULL, item_key VARCHAR(128) NOT NULL,
  loop_pass INT NOT NULL DEFAULT 0, attempt INT NOT NULL DEFAULT 1, status VARCHAR(32) NOT NULL DEFAULT 'pending',
  started_at DATETIME NULL, ended_at DATETIME NULL, duration_ms INT NULL, tokens_in INT NOT NULL DEFAULT 0,
  tokens_out INT NOT NULL DEFAULT 0, cost_micros BIGINT NOT NULL DEFAULT 0,
  executor_kind VARCHAR(16) NOT NULL DEFAULT 'model', model_id VARCHAR(128) NULL,
  cache_state VARCHAR(32) NOT NULL DEFAULT 'miss', ack_status VARCHAR(40) NULL, error JSON NULL,
  created_at DATETIME NOT NULL, PRIMARY KEY (org_id, execution_id),
  FOREIGN KEY (org_id, run_id) REFERENCES playbook_runs(org_id, run_id));
"""

AGE_GROUPS = [
    (1, "L1", "L1: الحروف وكلماتها ج1 · Letters & Words Pt 1"),
    (8, "L2", "L2: الحروف وكلماتها ج2 · Letters & Words Pt 2"),
    (2, "L3", "L3: كلماتي الأولى · First Words"),
    (3, "L4", "L4: ١٠٠٠ كلمة – جسمي، ملابسي، المهن · 1,000 Words – Body, Clothes, Jobs"),
]
SUBJECTS = [(1, "الحروف · Letters"), (2, "الأرقام · Numbers"), (3, "الألوان · Colours")]
ACHIEVEMENTS = [
    (1, "Consistent Learner", "1 day win streak", 10, 1),
    (2, "Letter Explorer", "Complete 10 letter bytes", 25, 2),
    (3, "Number Ninja", "Score 90+ on 5 number bytes", 40, 3),
]
FAMILIES = {
    "7f3a2c1e-0000-4000-8000-000000000001": ("amal", ["Yousef", "Lina", "Omar"]),
    "9b1d4e2f-0000-4000-8000-000000000002": ("amal", ["Sara"]),
    "c4e8a6b0-0000-4000-8000-000000000003": ("thurayya", ["Adam", "Maryam"]),
}

ORGS = {
    "org_acme": (
        "Acme Learning Ltd",
        [
            ("emp_writer", "Blog Writer", "content_writer", "running"),
            ("emp_research", "Research Analyst", "researcher", "idle"),
            ("emp_qa", "Content QA", "reviewer", "blocked"),
        ],
    ),
    "org_globex": (
        "Globex Media",
        [
            ("emp_social", "Social Editor", "content_writer", "running"),
            ("emp_seo", "SEO Scout", "researcher", "idle"),
        ],
    ),
}
MODELS = ["eu.anthropic.claude-haiku-4-5-20251001-v1:0", "qwen2.5:14b", "qwen2.5:32b"]
TERMINAL = {"failed": ["map_item_exhausted", "step_retries_exhausted"]}


def run(conn, ddl: str) -> None:
    for statement in filter(str.strip, ddl.split(";")):
        conn.execute(text(statement))


def seed_alqosh(conn) -> None:
    run(conn, ALQOSH_DDL)
    for row in AGE_GROUPS:
        conn.execute(
            text("INSERT INTO age_groups VALUES (:i, 'amal', :g, :t)"), dict(zip("igt", row))
        )
    for sid, label in SUBJECTS:
        conn.execute(
            text("INSERT INTO subjects VALUES (:i, :l, 'ar', 0, 'active', 'card', 'bytes')"),
            {"i": sid, "l": label},
        )
        for ch in range(1, 3):
            cid = sid * 10 + ch
            conn.execute(
                text("INSERT INTO chapters VALUES (:c, :s, :l, 'active')"),
                {"c": cid, "s": sid, "l": f"Chapter {ch}"},
            )
            for b in range(1, 4):
                conn.execute(
                    text("INSERT INTO content_bytes VALUES (:b, :c, :t, :o, 'solo', :l)"),
                    {
                        "b": cid * 10 + b,
                        "c": cid,
                        "t": RNG.choice(["skill", "one_vs_one", "boss_battle"]),
                        "o": b,
                        "l": f"Byte {b}",
                    },
                )
    for row in ACHIEVEMENTS:
        conn.execute(
            text("INSERT INTO achievements VALUES (:i, :m, :d, :c, :s)"), dict(zip("imdcs", row))
        )
    bytes_ = [r[0] for r in conn.execute(text("SELECT id FROM content_bytes"))]
    for parent, (app, kids) in FAMILIES.items():
        conn.execute(
            text("INSERT INTO parents VALUES (:p, :e, :a, :t)"),
            {"p": parent, "e": f"parent-{parent[:4]}@example.invalid", "a": app, "t": NOW},
        )
        for name in kids:
            child = str(uuid.UUID(int=RNG.getrandbits(128), version=4))
            skill = RNG.randint(35, 95)
            conn.execute(
                text(
                    "INSERT INTO children VALUES (:c, :p, :n, :g, '2018-05-20', :ag, NULL, :s, :a)"
                ),
                {
                    "c": child,
                    "p": parent,
                    "n": name,
                    "g": RNG.choice(["male", "female"]),
                    "ag": RNG.choice(AGE_GROUPS)[0],
                    "s": skill * 10,
                    "a": app,
                },
            )
            conn.execute(
                text("INSERT INTO user_streaks VALUES (:c, :d, :k)"),
                {"c": child, "d": RNG.randint(0, 21), "k": RNG.randint(20, 400)},
            )
            for b in RNG.sample(bytes_, 9):
                score = max(0, min(100, skill + RNG.randint(-20, 15)))
                conn.execute(
                    text(
                        "INSERT INTO content_byte_user (user_id, content_byte_id, status, top_score) "
                        "VALUES (:c, :b, :s, :t)"
                    ),
                    {
                        "c": child,
                        "b": b,
                        "s": "completed" if score >= 50 else "in-progress",
                        "t": score,
                    },
                )
            for aid, *_ in ACHIEVEMENTS[: RNG.randint(1, 3)]:
                conn.execute(
                    text("INSERT INTO user_achievements VALUES (:c, :a, :p, 'in_progress')"),
                    {"c": child, "a": aid, "p": RNG.randint(10, 95)},
                )


def seed_shepherd(conn) -> None:
    run(conn, SHEPHERD_DDL)
    for org, (name, employees) in ORGS.items():
        conn.execute(
            text(
                "INSERT INTO organisations (org_id, display_name, created_at) VALUES (:o, :n, :t)"
            ),
            {"o": org, "n": name, "t": NOW - timedelta(days=90)},
        )
        user = f"usr_{org}"
        conn.execute(
            text("INSERT INTO users VALUES (:u, :e, :n, :t)"),
            {"u": user, "e": f"ops@{org}.example.invalid", "n": "Ops Lead", "t": NOW},
        )
        conn.execute(
            text("INSERT INTO memberships VALUES (:o, :u, 'owner', 1, :t)"),
            {"o": org, "u": user, "t": NOW},
        )
        for emp, agent, role, status in employees:
            conn.execute(
                text(
                    "INSERT INTO employees (org_id, employee_id, role, agent_name, status, autonomy_level, "
                    "created_at) VALUES (:o, :e, :r, :a, :s, 2, :t)"
                ),
                {
                    "o": org,
                    "e": emp,
                    "r": role,
                    "a": agent,
                    "s": status,
                    "t": NOW - timedelta(days=60),
                },
            )
            for _ in range(RNG.randint(4, 9)):
                created = NOW - timedelta(days=RNG.randint(0, 20), hours=RNG.randint(0, 23))
                status_ = RNG.choices(["completed", "failed", "refused_budget"], [6, 2, 1])[0]
                run_id = f"run_{RNG.getrandbits(48):012x}"
                conn.execute(
                    text(
                        "INSERT INTO playbook_runs VALUES (:o, :r, :p, 1, :e, :s, :tr, :rb, NULL, 1000000, 600000, "
                        "200000, 40, :u, :c, :c, :end, NULL)"
                    ),
                    {
                        "o": org,
                        "r": run_id,
                        "p": RNG.choice(["blog_post", "weekly_digest", "seo_brief"]),
                        "e": emp,
                        "s": status_,
                        "tr": RNG.choice(TERMINAL[status_]) if status_ in TERMINAL else None,
                        "rb": "spend" if status_ == "refused_budget" else None,
                        "u": user,
                        "c": created,
                        "end": created + timedelta(minutes=RNG.randint(2, 40)),
                    },
                )
                for step in range(RNG.randint(2, 6)):
                    tin, tout = RNG.randint(1800, 2600), RNG.randint(2200, 3100)
                    model = RNG.choice(MODELS)
                    cost = 0 if model.startswith("qwen") else RNG.randint(15800, 19300)
                    conn.execute(
                        text(
                            "INSERT INTO node_executions (org_id, execution_id, run_id, node_id, item_key, status, "
                            "tokens_in, tokens_out, cost_micros, executor_kind, model_id, cache_state, ack_status, "
                            "created_at) VALUES (:o, :x, :r, :n, :k, :s, :ti, :to, :c, 'model', :m, :cs, 'accepted', :t)"
                        ),
                        {
                            "o": org,
                            "x": f"exe_{RNG.getrandbits(48):012x}",
                            "r": run_id,
                            "n": "plan" if step == 0 else "author",
                            "k": f"item-{step}",
                            "s": "failed" if status_ == "failed" and step == 0 else "succeeded",
                            "ti": tin,
                            "to": tout,
                            "c": cost,
                            "m": model,
                            "cs": RNG.choices(["miss", "exact_hit"], [3, 1])[0],
                            "t": created,
                        },
                    )


READERS = {
    "alqosh": (
        "alqosh_reader",
        [
            "age_groups",
            "children",
            "subjects",
            "chapters",
            "content_bytes",
            "content_byte_user",
            "user_streaks",
            "achievements",
            "user_achievements",
        ],
    ),
    "shepherd": ("shepherd_reader", ["employees", "playbook_runs", "node_executions"]),
}


def main() -> None:
    root = sys.argv[1].rstrip("/")
    admin = create_engine(root + "/mysql?charset=utf8mb4")
    with admin.begin() as conn:
        for db, (user, _tables) in READERS.items():
            conn.execute(text(f"DROP DATABASE IF EXISTS {db}"))
            conn.execute(
                text(f"CREATE DATABASE {db} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            )
            conn.execute(text(f"DROP USER IF EXISTS '{user}'@'%'"))
            conn.execute(text(f"CREATE USER '{user}'@'%' IDENTIFIED BY '{user}-local'"))
    for db, seed in (("alqosh", seed_alqosh), ("shepherd", seed_shepherd)):
        with create_engine(f"{root}/{db}?charset=utf8mb4").begin() as conn:
            seed(conn)
    with admin.begin() as conn:
        for db, (user, tables) in READERS.items():
            for table in tables:
                conn.execute(text(f"GRANT SELECT ON {db}.{table} TO '{user}'@'%'"))
    print(
        "seeded alqosh:",
        sum(len(k) for _, k in FAMILIES.values()),
        "children in",
        len(FAMILIES),
        "families; shepherd:",
        sum(len(e) for _, e in ORGS.values()),
        "employees in",
        len(ORGS),
        "orgs",
    )


if __name__ == "__main__":
    main()
