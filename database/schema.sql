-- ============================================================
-- CAMPUSFIX: CONSOLIDATED DATABASE SCHEMA
-- Supports fresh installations of the complete application.
-- ============================================================

-- ------------------------------------------------------------
-- 1. Student issue reports and AI assessments
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS issues (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,

    title TEXT NOT NULL
        CHECK(length(trim(title)) > 0),

    description TEXT NOT NULL
        CHECK(length(trim(description)) > 0),

    category TEXT NOT NULL DEFAULT 'Unclassified',

    location TEXT NOT NULL
        CHECK(length(trim(location)) > 0),

    severity TEXT NOT NULL DEFAULT 'Medium'
        CHECK(severity IN ('Low', 'Medium', 'High', 'Critical')),

    priority TEXT NOT NULL DEFAULT 'Medium'
        CHECK(priority IN ('Low', 'Medium', 'High', 'Critical')),

    image_path TEXT,

    status TEXT NOT NULL DEFAULT 'Submitted'
        CHECK(status IN (
            'Submitted', 'Under Review', 'Assigned',
            'In Progress', 'Resolved', 'Rejected'
        )),

    assigned_to TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,

    -- Original AI assessment
    ai_category TEXT,
    ai_severity TEXT,
    secondary_hazards TEXT NOT NULL DEFAULT '[]',
    ai_summary TEXT,
    ai_reason TEXT,
    safety_risk INTEGER NOT NULL DEFAULT 0,
    safety_risk_reason TEXT,
    ai_method TEXT
);

CREATE INDEX IF NOT EXISTS idx_issues_status
    ON issues(status);

CREATE INDEX IF NOT EXISTS idx_issues_category
    ON issues(category);

CREATE INDEX IF NOT EXISTS idx_issues_created_at
    ON issues(created_at DESC);


-- ------------------------------------------------------------
-- 2. Incident groups
-- Multiple student submissions can represent one incident.
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS issue_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,

    primary_issue_id INTEGER NOT NULL UNIQUE,

    duplicate_count INTEGER NOT NULL DEFAULT 0
        CHECK(duplicate_count >= 0),

    status TEXT NOT NULL DEFAULT 'Submitted'
        CHECK(status IN (
            'Submitted', 'Under Review', 'Assigned',
            'In Progress', 'Resolved', 'Rejected'
        )),

    priority TEXT NOT NULL DEFAULT 'Medium'
        CHECK(priority IN ('Low', 'Medium', 'High', 'Critical')),

    assigned_team TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,

    FOREIGN KEY (primary_issue_id)
        REFERENCES issues(issue_id) ON DELETE CASCADE
);


-- ------------------------------------------------------------
-- 3. Reports associated with incident groups
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS issue_group_members (
    group_id INTEGER NOT NULL,
    issue_id INTEGER NOT NULL UNIQUE,

    FOREIGN KEY (group_id)
        REFERENCES issue_groups(group_id) ON DELETE CASCADE,

    FOREIGN KEY (issue_id)
        REFERENCES issues(issue_id) ON DELETE CASCADE,

    PRIMARY KEY (group_id, issue_id)
);


-- ------------------------------------------------------------
-- 4. Cached semantic embeddings for duplicate detection
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS issue_embeddings (
    issue_id INTEGER PRIMARY KEY,

    model_name TEXT NOT NULL,
    embedding_json TEXT NOT NULL,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (issue_id)
        REFERENCES issues(issue_id) ON DELETE CASCADE
);


-- ------------------------------------------------------------
-- 5. Incident activity and management history
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS incident_activity (
    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,

    group_id INTEGER NOT NULL,

    event_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (group_id)
        REFERENCES issue_groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_activity_group
    ON incident_activity(group_id, created_at DESC);


-- ------------------------------------------------------------
-- 6. Safety response acknowledgement history
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS safety_response_log (
    response_id INTEGER PRIMARY KEY AUTOINCREMENT,

    issue_id INTEGER,
    group_id INTEGER,

    acknowledged_by TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    notified_party TEXT,
    notes TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (
        (issue_id IS NOT NULL AND group_id IS NULL)
        OR
        (issue_id IS NULL AND group_id IS NOT NULL)
    ),

    FOREIGN KEY (issue_id)
        REFERENCES issues(issue_id) ON DELETE CASCADE,

    FOREIGN KEY (group_id)
        REFERENCES issue_groups(group_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_safety_response_issue
    ON safety_response_log(issue_id);

CREATE INDEX IF NOT EXISTS idx_safety_response_group
    ON safety_response_log(group_id);

CREATE INDEX IF NOT EXISTS idx_safety_response_created
    ON safety_response_log(created_at DESC);


-- ============================================================
-- 7. Persistent duplicate review queue
-- ============================================================

CREATE TABLE IF NOT EXISTS duplicate_review_candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,

    new_issue_id INTEGER NOT NULL,
    matched_issue_id INTEGER NOT NULL,

    similarity REAL NOT NULL
        CHECK (similarity >= -1.0 AND similarity <= 1.0),

    decision TEXT NOT NULL DEFAULT 'Pending'
        CHECK (
            decision IN (
                'Pending',
                'Confirmed',
                'Rejected',
                'New Incident'
            )
        ),

    reviewed_by TEXT,
    reviewed_at TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (new_issue_id <> matched_issue_id),

    UNIQUE (new_issue_id, matched_issue_id),

    FOREIGN KEY (new_issue_id)
        REFERENCES issues(issue_id) ON DELETE CASCADE,

    FOREIGN KEY (matched_issue_id)
        REFERENCES issues(issue_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_duplicate_review_decision
    ON duplicate_review_candidates(decision);

CREATE INDEX IF NOT EXISTS idx_duplicate_review_new_issue
    ON duplicate_review_candidates(new_issue_id);

-- ============================================================
-- 8. Authentication (users table, issues.submitted_by,
--    incident_activity.changed_by)
--
-- These are defined in database/migrations.py, not here, because
-- existing tables cannot be altered with CREATE ... IF NOT EXISTS.
-- initialize_database() applies this file first and then the
-- migrations, so fresh and existing databases follow one path.
-- ============================================================

-- ============================================================
-- END OF CONSOLIDATED SCHEMA
-- ============================================================
