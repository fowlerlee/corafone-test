CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE agreements (
  agreement_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  debt_id           TEXT NOT NULL,
  creditor_name     TEXT NOT NULL,
  call_id           TEXT NOT NULL,
  type              TEXT CHECK (type IN ('full','downpayment_plus_one',
                                        'settlement','payment_plan')),
  principal         NUMERIC(10,2) NOT NULL,
  total_agreed      NUMERIC(10,2) NOT NULL,
  schedule          JSONB NOT NULL,
  cadence           TEXT,
  consumer_name     TEXT,
  consumer_phone    TEXT,
  consent_phrase    TEXT NOT NULL,
  created_at        TIMESTAMPTZ DEFAULT now(),
  status            TEXT DEFAULT 'lodged'
);

CREATE TABLE compliance_breaches (
  breach_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  call_id           TEXT NOT NULL,
  rule              TEXT NOT NULL,
  transcript_excerpt TEXT,
  detected_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_agreements_debt ON agreements(debt_id);
CREATE INDEX idx_breaches_call ON compliance_breaches(call_id);

-- Consumer identity lookup table (for debt collector verification)
CREATE TABLE consumers (
  consumer_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  debt_id        TEXT UNIQUE NOT NULL,
  consumer_name  TEXT NOT NULL,
  consumer_phone TEXT,
  last_4_ssn     TEXT,
  dob            TEXT,
  principal      NUMERIC(10,2) NOT NULL DEFAULT 1000.00,
  days_delinquent INT NOT NULL DEFAULT 180,
  created_at     TIMESTAMPTZ DEFAULT now()
);

-- Insert test consumer for LiveKit Console testing
INSERT INTO consumers (debt_id, consumer_name, last_4_ssn, dob, principal, days_delinquent)
VALUES ('TEST-001', 'Test Consumer', '1234', '01/15/1990', 1000.00, 180)
ON CONFLICT (debt_id) DO NOTHING;
