-- Generated from app/models.py by docs/schema/make_schema_docs.py. Do not edit by hand.
-- PostgreSQL rendering of the same schema (production target). In SQLite tables are created at app startup with Base.metadata.create_all (no migrations yet).

CREATE TYPE subjecttype AS ENUM ('COMPANY', 'ACCOUNT_OFFICER');
CREATE TYPE idtype AS ENUM ('UEN', 'NRIC', 'FIN', 'PASSPORT');
CREATE TYPE inventorystatus AS ENUM ('AVAILABLE', 'PAIRED', 'RESERVED', 'ASSIGNED');
CREATE TYPE simtype AS ENUM ('ESIM', 'PHYSICAL');
CREATE TYPE linestatus AS ENUM ('PENDING', 'PROVISIONED', 'FAILED');
CREATE TYPE signupstatus AS ENUM ('DRAFT', 'SUBMITTED', 'COMPLETED', 'PARTIALLY_FAILED', 'FAILED');

-- ao_sessions: Account officer sessions: only a SHA-256 of the token is stored; expiry and revocation.
CREATE TABLE ao_sessions (
	id SERIAL NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_hash)
);
CREATE INDEX ix_ao_sessions_email ON ao_sessions (email);

-- api_clients: Partner / AI-agent API keys for /gateway/v1: prefix + SHA-256 of the secret, scopes, rate limit.
CREATE TABLE api_clients (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	key_prefix VARCHAR(16) NOT NULL, 
	key_hash VARCHAR(64) NOT NULL, 
	scopes TEXT NOT NULL, 
	rate_limit_per_min INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (key_prefix)
);

-- blacklist_entries: Blocked companies (UEN) and account officers (NRIC/FIN/passport), with validity dates.
CREATE TABLE blacklist_entries (
	id SERIAL NOT NULL, 
	subject_type subjecttype NOT NULL, 
	id_type idtype NOT NULL, 
	id_value VARCHAR(40) NOT NULL, 
	reason TEXT NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_blacklist_entries_id_value ON blacklist_entries (id_value);

-- category_allocations: Who may use a SIM category: one company (uen) or all companies (uen NULL). Inactive hides it.
CREATE TABLE category_allocations (
	id SERIAL NOT NULL, 
	category VARCHAR(40) NOT NULL, 
	uen VARCHAR(10), 
	description VARCHAR(200) NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (category)
);
CREATE INDEX ix_category_allocations_uen ON category_allocations (uen);

-- companies: Corporate customer, one per UEN. Saved on first validation; not overwritten after its first order.
CREATE TABLE companies (
	id SERIAL NOT NULL, 
	uen VARCHAR(10) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	address TEXT NOT NULL, 
	contract_key VARCHAR(512), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (uen)
);

-- gateway_audit_log: One row per inbound /gateway/v1 request.
CREATE TABLE gateway_audit_log (
	id SERIAL NOT NULL, 
	client_id INTEGER, 
	method VARCHAR(10) NOT NULL, 
	path VARCHAR(500) NOT NULL, 
	status_code INTEGER NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	remote_ip VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_gateway_audit_log_client_id ON gateway_audit_log (client_id);
CREATE INDEX ix_gateway_audit_log_created_at ON gateway_audit_log (created_at);

-- merchants: Delegated merchant codes shown in the AO step. Seeded with CORP-DIRECT-01.
CREATE TABLE merchants (
	id SERIAL NOT NULL, 
	code VARCHAR(40) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

-- number_inventory: Mobile numbers (MSISDN) by category. Added as AVAILABLE; paired by SIM uploads.
CREATE TABLE number_inventory (
	id SERIAL NOT NULL, 
	msisdn VARCHAR(15) NOT NULL, 
	category VARCHAR(40) NOT NULL, 
	status inventorystatus NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (msisdn)
);
CREATE INDEX ix_number_inventory_category ON number_inventory (category);
CREATE INDEX ix_number_inventory_status ON number_inventory (status);

-- otp_challenges: Sign-in codes: only an HMAC of the code is stored; expiry, attempts, consumed time.
CREATE TABLE otp_challenges (
	id SERIAL NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	code_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	attempts INTEGER NOT NULL, 
	consumed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_otp_challenges_created_at ON otp_challenges (created_at);
CREATE INDEX ix_otp_challenges_email ON otp_challenges (email);

-- outbound_call_log: One row per outbound call attempt to network, billing or AI.
CREATE TABLE outbound_call_log (
	id SERIAL NOT NULL, 
	upstream VARCHAR(40) NOT NULL, 
	operation VARCHAR(80) NOT NULL, 
	attempt INTEGER NOT NULL, 
	outcome VARCHAR(20) NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_outbound_call_log_created_at ON outbound_call_log (created_at);
CREATE INDEX ix_outbound_call_log_upstream ON outbound_call_log (upstream);

-- plans: Monthly plans offered (Ray, Flash, Glare). Seeded at startup.
CREATE TABLE plans (
	id SERIAL NOT NULL, 
	code VARCHAR(40) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	monthly_price NUMERIC(10, 2) NOT NULL, 
	data_gb INTEGER NOT NULL, 
	description TEXT NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

-- upload_batches: Every SIM or number file uploaded: who, from where, counts and every row's result (rows_json).
CREATE TABLE upload_batches (
	id SERIAL NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	filename VARCHAR(255) NOT NULL, 
	source VARCHAR(20) NOT NULL, 
	uploaded_by VARCHAR(254) NOT NULL, 
	default_category VARCHAR(40), 
	signup_id VARCHAR(36), 
	total_rows INTEGER NOT NULL, 
	accepted INTEGER NOT NULL, 
	rejected INTEGER NOT NULL, 
	paired INTEGER NOT NULL, 
	rows_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_upload_batches_created_at ON upload_batches (created_at);

-- account_officers: Company representative (NRIC/FIN/passport) who manages the lines.
CREATE TABLE account_officers (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	full_name VARCHAR(200) NOT NULL, 
	id_type idtype NOT NULL, 
	id_number VARCHAR(40) NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	mobile VARCHAR(20) NOT NULL, 
	designation VARCHAR(100) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (company_id, id_type, id_number), 
	FOREIGN KEY(company_id) REFERENCES companies (id)
);

-- sim_inventory: SIM cards (ICCID) by category and type, with eSIM activation data and the paired number.
CREATE TABLE sim_inventory (
	id SERIAL NOT NULL, 
	iccid VARCHAR(20) NOT NULL, 
	sim_type simtype NOT NULL, 
	category VARCHAR(40) NOT NULL, 
	status inventorystatus NOT NULL, 
	smdp_address VARCHAR(255), 
	matching_id VARCHAR(64), 
	number_id INTEGER, 
	paired_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (iccid), 
	UNIQUE (number_id), 
	FOREIGN KEY(number_id) REFERENCES number_inventory (id)
);
CREATE INDEX ix_sim_inventory_category ON sim_inventory (category);
CREATE INDEX ix_sim_inventory_status ON sim_inventory (status);

-- corporate_accounts: Account created on submit (account number CA…), linked to billing.
CREATE TABLE corporate_accounts (
	id SERIAL NOT NULL, 
	account_number VARCHAR(20) NOT NULL, 
	company_id INTEGER NOT NULL, 
	account_officer_id INTEGER NOT NULL, 
	merchant_code VARCHAR(40) NOT NULL, 
	billing_account_ref VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (account_number), 
	FOREIGN KEY(company_id) REFERENCES companies (id), 
	FOREIGN KEY(account_officer_id) REFERENCES account_officers (id)
);

-- lines: Provisioned mobile line: SIM + number + plan, with network/billing references and status.
CREATE TABLE lines (
	id SERIAL NOT NULL, 
	account_id INTEGER NOT NULL, 
	sim_id INTEGER NOT NULL, 
	number_id INTEGER NOT NULL, 
	plan_id INTEGER NOT NULL, 
	status linestatus NOT NULL, 
	network_ref VARCHAR(64), 
	billing_ref VARCHAR(64), 
	last_error TEXT, 
	esim_qr_sent_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES corporate_accounts (id), 
	UNIQUE (sim_id), 
	FOREIGN KEY(sim_id) REFERENCES sim_inventory (id), 
	UNIQUE (number_id), 
	FOREIGN KEY(number_id) REFERENCES number_inventory (id), 
	FOREIGN KEY(plan_id) REFERENCES plans (id)
);
CREATE INDEX ix_lines_account_id ON lines (account_id);

-- signups: Draft order an account officer fills in step by step; turns into Company / Account / Lines on submit.
CREATE TABLE signups (
	id VARCHAR(36) NOT NULL, 
	status signupstatus NOT NULL, 
	uen VARCHAR(10) NOT NULL, 
	company_name VARCHAR(200) NOT NULL, 
	company_address TEXT NOT NULL, 
	category VARCHAR(40), 
	contract_key VARCHAR(512), 
	contract_filename VARCHAR(255), 
	ao_full_name VARCHAR(200), 
	ao_id_type idtype, 
	ao_id_number VARCHAR(40), 
	ao_email VARCHAR(254), 
	ao_mobile VARCHAR(20), 
	ao_designation VARCHAR(100), 
	merchant_code VARCHAR(40), 
	owner_email VARCHAR(254) NOT NULL, 
	contract_ai_review TEXT, 
	account_id INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	submitted_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES corporate_accounts (id)
);
CREATE INDEX ix_signups_owner_email ON signups (owner_email);

-- cart_lines: SIM + number + plan reserved in a sign-up's cart. Kept after submit.
CREATE TABLE cart_lines (
	id SERIAL NOT NULL, 
	signup_id VARCHAR(36) NOT NULL, 
	sim_id INTEGER NOT NULL, 
	number_id INTEGER NOT NULL, 
	plan_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (sim_id), 
	FOREIGN KEY(signup_id) REFERENCES signups (id), 
	FOREIGN KEY(sim_id) REFERENCES sim_inventory (id), 
	FOREIGN KEY(number_id) REFERENCES number_inventory (id), 
	FOREIGN KEY(plan_id) REFERENCES plans (id)
);
CREATE INDEX ix_cart_lines_signup_id ON cart_lines (signup_id);
