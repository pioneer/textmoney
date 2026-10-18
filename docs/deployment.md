# Static Web Deployment

`run-web` serves a live Python process for development. `build-web` creates a
different deployment: static Flutter/Pyodide files, with Python executing in the
browser. No Python server, AWS SDK, or backend credentials belong in that bundle.
All packaged source/configuration is publicly downloadable. Browser sandbox,
CORS, supported wheels, storage quotas, and secure-context rules still apply.

```sh
uv run inv build-web
uv run inv serve-web
```

Open http://localhost:8000. Inspect Console/Network for Python/client exceptions,
failed assets, MIME types, and CORS errors. For subdirectory hosting build with
`uv run inv build-web --base-url=/my-app/` and serve it at that same URL prefix;
the simple root `serve-web` preview is for root builds. Hash routes avoid requiring
server rewrites for application routes. They do not repair a wrong asset base URL.

## S3

Install AWS CLI v2 separately and authenticate outside the repository using SSO,
an AWS profile, environment credentials, or a workload role. Do not put access
keys in files here, in the browser app, or in pyproject.toml. Prefer short-lived
credentials and least privilege scoped to the deployment bucket/prefix.

Set `S3_BUCKET` to a bare bucket name. `S3_PREFIX` is optional and selects an
app-only folder; `AWS_PROFILE` and `AWS_REGION` are optional. Example for a POSIX
shell, with an already configured non-secret profile:

```sh
export S3_BUCKET=my-app-site
export S3_PREFIX=my-app
export AWS_PROFILE=my-deployment-profile
export AWS_REGION=us-east-1
uv run inv deploy-web
```

In PowerShell use `$env:S3_BUCKET = "my-app-site"` and the same form for the
other variables, then run the same Invoke task. `.env.example` is not loaded.

The task builds with the corresponding base URL, checks for AWS CLI, validates
the bucket and `build/web/index.html`, then invokes:

```text
aws s3 sync build/web s3://BUCKET/PREFIX/ --delete
```

**Destructive destination:** `--delete` removes remote objects absent from the
local build. An empty prefix targets the entire bucket. Use a dedicated app
bucket/prefix, inspect environment values before running, and enable versioning
or recovery appropriate to your environment. This is not an atomic deployment;
use a release-prefix/traffic-switch design if the product needs atomic updates.
The task does not create buckets, permissions, distributions, or credentials.

For an S3 website endpoint, enable static website hosting with `index.html` and
deliberately configure public-read access only for these public build files.
Do not broadly disable security controls on a bucket containing private data.
S3 website endpoints use HTTP. Prefer HTTPS through CloudFront; a private S3 REST
origin with Origin Access Control avoids public bucket access. Configure default
root object `index.html`, any required prefix-root mapping, and correct MIME
types. Do not use a blanket 200 error rewrite to hide missing assets.

If CloudFront fronts the site, invalidate changed entry files after syncing or
use a deliberate cache/version policy. The task does not invalidate CloudFront.
Check `index.html`, manifests, service-worker behavior, and runtime asset caching
after deployments. HTTPS is needed for many browser APIs beyond localhost.
Test the final public prefix URL, a hard refresh, hash navigation, icons, and
Console/Network errors. Never test deployment by uploading with real credentials
from an automated unit test.