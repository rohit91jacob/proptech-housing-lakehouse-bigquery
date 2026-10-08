# Google Cloud setup

> **Status.** These steps follow Google's documented setup, but they have **not** been
> executed for this repository yet. No GCP project was available while it was built. CI
> skips its BigQuery jobs, with a notice, until the variables and secrets below exist.

## 1. Project with the BigQuery sandbox

1. Sign in at <https://console.cloud.google.com/bigquery> and create a project. The sandbox
   is enabled automatically when the project has no billing account.
2. Note the **project ID**. It becomes `PROPTECH_BQ_PROJECT` and the repository variable
   `GCP_PROJECT_ID`.

The loader creates its datasets (`raw_zillow`, `raw_reference`, `ops`, `staging`,
`intermediate`, `marts`, `seeds`) on first run, in `PROPTECH_BQ_LOCATION` (default `US`).

## 2. Service account

```bash
PROJECT_ID=your-project-id
gcloud iam service-accounts create proptech-pipeline --project "$PROJECT_ID" \
  --display-name "proptech pipeline"
SA="proptech-pipeline@${PROJECT_ID}.iam.gserviceaccount.com"
for role in roles/bigquery.dataEditor roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$SA" --role "$role"
done
```

`bigquery.dataEditor` lets the account create datasets and tables and run load jobs.
`bigquery.jobUser` lets it run query and load jobs.

## 3a. Keyless CI with Workload Identity Federation (recommended)

```bash
gcloud iam workload-identity-pools create github --project "$PROJECT_ID" --location global
gcloud iam workload-identity-pools providers create-oidc github \
  --project "$PROJECT_ID" --location global --workload-identity-pool github \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition "assertion.repository == 'rohit91jacob/proptech-housing-lakehouse-bigquery'"
POOL=$(gcloud iam workload-identity-pools describe github --project "$PROJECT_ID" --location global --format 'value(name)')
gcloud iam service-accounts add-iam-policy-binding "$SA" --project "$PROJECT_ID" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/${POOL}/attribute.repository/rohit91jacob/proptech-housing-lakehouse-bigquery"
gcloud iam workload-identity-pools providers describe github --project "$PROJECT_ID" \
  --location global --workload-identity-pool github --format 'value(name)'
```

Set these **repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
|---|---|
| `GCP_PROJECT_ID` | project ID |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | output of the last command |
| `GCP_SERVICE_ACCOUNT` | `proptech-pipeline@<project>.iam.gserviceaccount.com` |
| `GCP_LOCATION` | optional, default `US` |

## 3b. Service-account key (fallback)

```bash
gcloud iam service-accounts keys create proptech-sa.json --iam-account "$SA"
```

Store the file's contents as the **secret** `GCP_SA_KEY`, and set the `GCP_PROJECT_ID`
variable. Don't set the WIF variables too: the auth step accepts exactly one method. For
local runs, `export GOOGLE_APPLICATION_CREDENTIALS=$PWD/proptech-sa.json`. `*-sa.json` is
git-ignored.

> An API key (`AIza...`) won't work. BigQuery rejects API keys with
> `401 API keys are not supported by this API`.

## 4. First run

```bash
export PROPTECH_TARGET=bigquery PROPTECH_ENV=sandbox PROPTECH_BQ_PROJECT=$PROJECT_ID DBT_TARGET=sandbox
uv run proptech ingest --dry-run      # fetch + validate only, writes nothing
uv run proptech ingest
uv run dbt build --project-dir dbt --profiles-dir dbt
uv run proptech budget
```

Or trigger **Actions → pipeline → Run workflow**.

## 5. Optional: archive raw vintages to Cloud Storage (billing-enabled projects)

Set `PROPTECH_RAW_ARCHIVE_URI=gs://<bucket>/proptech/archive` (repository variable
`RAW_ARCHIVE_URI`). On the bucket, grant the service account `roles/storage.objectCreator`
plus `roles/storage.objectViewer`; the viewer role is needed for the existence check.
Objects are written with `if_generation_match=0`, so an existing vintage is never
overwritten.
