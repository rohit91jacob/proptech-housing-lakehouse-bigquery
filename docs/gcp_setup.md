# Google Cloud setup

> **Status.**
> - Live auth is the service-account key (3b). Project `proptech-housing` is a sandbox with no
>   billing, and `proptech-pipeline@proptech-housing.iam.gserviceaccount.com` holds BigQuery
>   Data Editor and Job User. The repository variable `GCP_PROJECT_ID=proptech-housing` is set.
> - Workload Identity Federation (3a) is wired into the workflows but not configured yet. When
>   its two variables exist, the workflows switch to it automatically.

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

GitHub issues each workflow run a short-lived OIDC token, and Google exchanges it for
temporary credentials. No key is stored anywhere, so there is nothing to expire, leak or
rotate. This takes about 10 minutes in the console, and needs an owner/IAM-admin login rather
than the pipeline's service account.

**Console steps** (project `proptech-housing`):

1. Open **IAM & Admin → Workload Identity Federation**
   (<https://console.cloud.google.com/iam-admin/workload-identity-pools>). If prompted, click
   **Enable APIs**; this enables the IAM Credentials APIs.
2. Click **Create pool**. Name: `github`, Pool ID: `github`. Click **Continue**.
3. **Add a provider to pool:** choose provider **OpenID Connect (OIDC)**.
   - Provider name: `github`, Provider ID: `github`.
   - Issuer (URL): `https://token.actions.githubusercontent.com`.
   - Audiences: **Default audience**.
   - Click **Continue**.
4. **Configure provider attributes:**
   - Mapping: `google.subject` = `assertion.sub`. Click **Add mapping** and set
     `attribute.repository` = `assertion.repository`.
   - **Attribute conditions → Add condition:**
     `assertion.repository == 'rohit91jacob/proptech-housing-lakehouse-bigquery'`. This
     condition is what stops any other GitHub repository from using the pool.
   - Click **Save**.
5. Open the new pool and click **Grant access** (or **Connected service accounts → Grant
   access**).
   - Choose **Grant access using service account impersonation**.
   - Service account: `proptech-pipeline@proptech-housing.iam.gserviceaccount.com`.
   - Principals: attribute name `repository`, value
     `rohit91jacob/proptech-housing-lakehouse-bigquery`.
   - Click **Save**. If asked to configure an application, dismiss it.

   This grants `roles/iam.workloadIdentityUser` on the service account to
   `principalSet://iam.googleapis.com/projects/<number>/locations/global/workloadIdentityPools/github/attribute.repository/rohit91jacob/proptech-housing-lakehouse-bigquery`.
6. Open the pool's **Providers** tab, click `github`, and copy the **resource name**. It looks
   like `projects/123456789012/locations/global/workloadIdentityPools/github/providers/github`.
   It uses the project *number*, not the ID.
7. Send the maintainer, or set yourself under **Settings → Secrets and variables → Actions →
   Variables**:

   | Variable | Value |
   |---|---|
   | `GCP_WORKLOAD_IDENTITY_PROVIDER` | the resource name from step 6 |
   | `GCP_SERVICE_ACCOUNT` | `proptech-pipeline@proptech-housing.iam.gserviceaccount.com` |
   | `GCP_PROJECT_ID` | `proptech-housing` (already set) |

8. Run **Actions → pipeline → Run workflow**. The log must show the step
   `authenticate (Workload Identity Federation, keyless)` and a passing
   `credential health check`.
9. Only after that run is green, retire the key:
   - **IAM & Admin → Service accounts → proptech-pipeline → Keys**: delete the key.
   - **GitHub → Settings → Secrets and variables → Actions**: delete the `GCP_SA_KEY` secret.
   - Delete the local copy of the key file.

<details><summary>Equivalent gcloud commands</summary>

```bash
PROJECT_ID=proptech-housing
SA="proptech-pipeline@${PROJECT_ID}.iam.gserviceaccount.com"
REPO=rohit91jacob/proptech-housing-lakehouse-bigquery
gcloud iam workload-identity-pools create github --project "$PROJECT_ID" --location global
gcloud iam workload-identity-pools providers create-oidc github \
  --project "$PROJECT_ID" --location global --workload-identity-pool github \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition "assertion.repository == '${REPO}'"
POOL=$(gcloud iam workload-identity-pools describe github --project "$PROJECT_ID" --location global --format 'value(name)')
gcloud iam service-accounts add-iam-policy-binding "$SA" --project "$PROJECT_ID" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/${POOL}/attribute.repository/${REPO}"
gcloud iam workload-identity-pools providers describe github --project "$PROJECT_ID" \
  --location global --workload-identity-pool github --format 'value(name)'
```

</details>

The workflows prefer WIF whenever both variables are set. A leftover `GCP_SA_KEY` is
ignored, so switching over needs no workflow edit.

## 3b. Service-account key (fallback)

```bash
gcloud iam service-accounts keys create proptech-sa.json --iam-account "$SA"
```

Store the file's contents as the **secret** `GCP_SA_KEY`. `GCP_PROJECT_ID` is optional on
this path: the workflows fall back to the key's own project, which `google-github-actions/auth`
exports as `GOOGLE_CLOUD_PROJECT`. If the WIF variables are also set, WIF takes precedence and
the key is ignored. For
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
