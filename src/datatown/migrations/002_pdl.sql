CREATE SCHEMA IF NOT EXISTS pdl;

CREATE TABLE IF NOT EXISTS pdl.companies (
    id text NOT NULL,
    website text,
    name text NOT NULL,
    founded integer,
    size text,
    locality text,
    region text,
    country text,
    industry text,
    linkedin_url text,
    CONSTRAINT companies_pkey PRIMARY KEY (id),
    CONSTRAINT companies_id_nonempty CHECK (id <> ''),
    CONSTRAINT companies_name_nonempty CHECK (name <> '')
);

CREATE INDEX IF NOT EXISTS companies_website_idx
    ON pdl.companies (website)
    WHERE website IS NOT NULL;

CREATE INDEX IF NOT EXISTS companies_linkedin_url_idx
    ON pdl.companies (linkedin_url)
    WHERE linkedin_url IS NOT NULL;

COMMENT ON TABLE pdl.companies IS
    'Current queryable snapshot of the People Data Labs company dataset.';

COMMENT ON COLUMN pdl.companies.website IS
    'Raw PDL website value; it is not guaranteed to be a bare domain.';
