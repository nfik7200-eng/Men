-- Exact schema produced by migration v1 (BIP 0.1, commit f583761). Used to test v1->v2 upgrades.
CREATE TABLE activity_log (
	id INTEGER NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	summary VARCHAR(300) NOT NULL, 
	created_at VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_activity_log PRIMARY KEY (id)
);
CREATE TABLE app_metadata (
	"key" VARCHAR(64) NOT NULL, 
	value TEXT NOT NULL, 
	updated_at VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_app_metadata PRIMARY KEY ("key")
);
CREATE TABLE evidence_refs (
	id INTEGER NOT NULL, 
	finding_pk INTEGER NOT NULL, 
	evidence_id VARCHAR(128) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	locator TEXT NOT NULL, 
	verified BOOLEAN NOT NULL, 
	provenance_json TEXT, 
	CONSTRAINT pk_evidence_refs PRIMARY KEY (id), 
	CONSTRAINT uq_evidence_refs_finding_pk_evidence_id UNIQUE (finding_pk, evidence_id), 
	CONSTRAINT fk_evidence_refs_finding_pk_findings FOREIGN KEY(finding_pk) REFERENCES findings (id) ON DELETE CASCADE
);
CREATE TABLE findings (
	id INTEGER NOT NULL, 
	investigation_id INTEGER, 
	finding_id VARCHAR(128) NOT NULL, 
	finding_type VARCHAR(64) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	detected_at VARCHAR(32) NOT NULL, 
	payload_json TEXT NOT NULL, 
	created_at VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_findings PRIMARY KEY (id), 
	CONSTRAINT fk_findings_investigation_id_investigations FOREIGN KEY(investigation_id) REFERENCES investigations (id) ON DELETE CASCADE, 
	CONSTRAINT uq_findings_finding_id UNIQUE (finding_id)
);
CREATE TABLE investigations (
	id INTEGER NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	description TEXT, 
	status VARCHAR(16) NOT NULL, 
	subject_chain VARCHAR(32), 
	subject_address VARCHAR(64), 
	created_at VARCHAR(32) NOT NULL, 
	updated_at VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_investigations PRIMARY KEY (id), 
	CONSTRAINT ck_investigations_status CHECK (status IN ('open', 'in_progress', 'closed')), 
	CONSTRAINT ck_investigations_subject_pair CHECK ((subject_chain IS NULL) = (subject_address IS NULL))
);
CREATE TABLE provider_checks (
	provider_id VARCHAR(64) NOT NULL, 
	last_check_at VARCHAR(32), 
	last_success_at VARCHAR(32), 
	last_outcome VARCHAR(32), 
	CONSTRAINT pk_provider_checks PRIMARY KEY (provider_id)
);
CREATE TABLE schema_migrations (
	version INTEGER NOT NULL, 
	applied_at VARCHAR(32) NOT NULL, 
	description VARCHAR(200) NOT NULL, 
	CONSTRAINT pk_schema_migrations PRIMARY KEY (version)
);
CREATE TABLE transactions (
	id INTEGER NOT NULL, 
	chain VARCHAR(32) NOT NULL, 
	tx_id VARCHAR(100) NOT NULL, 
	block_number INTEGER, 
	slot INTEGER, 
	block_time VARCHAR(32), 
	from_address VARCHAR(64), 
	to_address VARCHAR(64), 
	status VARCHAR(16), 
	provenance_json TEXT NOT NULL, 
	stored_at VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_transactions PRIMARY KEY (id), 
	CONSTRAINT uq_transactions_chain_tx_id UNIQUE (chain, tx_id), 
	CONSTRAINT ck_transactions_one_position CHECK ((block_number IS NULL) != (slot IS NULL))
);
CREATE TABLE transfers (
	id INTEGER NOT NULL, 
	chain VARCHAR(32) NOT NULL, 
	tx_id VARCHAR(100) NOT NULL, 
	event_key VARCHAR(32) NOT NULL, 
	block_number INTEGER, 
	slot INTEGER, 
	standard VARCHAR(32) NOT NULL, 
	token VARCHAR(64), 
	from_address VARCHAR(64), 
	to_address VARCHAR(64), 
	amount_raw VARCHAR(80) NOT NULL, 
	decimals INTEGER, 
	token_id VARCHAR(80), 
	classification VARCHAR(32) NOT NULL, 
	provenance_json TEXT NOT NULL, 
	stored_at VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_transfers PRIMARY KEY (id), 
	CONSTRAINT uq_transfers_chain_tx_id_event_key UNIQUE (chain, tx_id, event_key), 
	CONSTRAINT ck_transfers_one_position CHECK ((block_number IS NULL) != (slot IS NULL))
);
CREATE TABLE wallets (
	id INTEGER NOT NULL, 
	chain VARCHAR(32) NOT NULL, 
	address VARCHAR(64) NOT NULL, 
	first_observed_at VARCHAR(32), 
	last_observed_at VARCHAR(32), 
	first_provenance_json TEXT NOT NULL, 
	created_at VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_wallets PRIMARY KEY (id), 
	CONSTRAINT uq_wallets_chain_address UNIQUE (chain, address)
);
CREATE TABLE watchlist_entries (
	id INTEGER NOT NULL, 
	watchlist_id INTEGER NOT NULL, 
	chain VARCHAR(32) NOT NULL, 
	address VARCHAR(64) NOT NULL, 
	note TEXT, 
	added_at VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_watchlist_entries PRIMARY KEY (id), 
	CONSTRAINT uq_watchlist_entries_watchlist_id_chain_address UNIQUE (watchlist_id, chain, address), 
	CONSTRAINT fk_watchlist_entries_watchlist_id_watchlists FOREIGN KEY(watchlist_id) REFERENCES watchlists (id) ON DELETE CASCADE
);
CREATE TABLE watchlists (
	id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	description TEXT, 
	created_at VARCHAR(32) NOT NULL, 
	updated_at VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_watchlists PRIMARY KEY (id), 
	CONSTRAINT uq_watchlists_name UNIQUE (name)
);
CREATE INDEX ix_activity_log_created_at ON activity_log (created_at);
CREATE INDEX ix_findings_detected_at ON findings (detected_at);
CREATE INDEX ix_investigations_status ON investigations (status);
CREATE INDEX ix_transactions_chain_from_address ON transactions (chain, from_address);
CREATE INDEX ix_transactions_chain_to_address ON transactions (chain, to_address);
CREATE INDEX ix_transfers_chain_from_address ON transfers (chain, from_address);
CREATE INDEX ix_transfers_chain_to_address ON transfers (chain, to_address);
CREATE INDEX ix_transfers_chain_token ON transfers (chain, token);
CREATE INDEX ix_watchlist_entries_chain_address ON watchlist_entries (chain, address);
