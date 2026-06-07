# TrueFoundry gateway configuration

Manifests applied via `tfy apply -f <file>`. Apply in this order:

```bash
# 1. Primary VM — the model TardigradeCS targets by default
tfy apply -f tardigrade_primary.yaml

# 2. Chaos VMs — pre-broken targets the chaos engine swaps to
tfy apply -f tardigrade_chaos_primary.yaml
tfy apply -f tardigrade_chaos_ratelimit.yaml
tfy apply -f tardigrade_chaos_cascade.yaml
```

The chaos VMs reference broken provider accounts (`anthropic-broken/...`,
`openai-broken/...`). Create those as separate provider-account integrations
with deliberately invalid API keys. They produce *real* 4xx responses from
the upstream — the fallback chain you see in the observability tab is the
gateway doing actual recovery, not the app faking it.

## Once AWS Bedrock model-access is approved

1. Add a `bedrock` provider account integration in the TrueFoundry UI with
   your AWS credentials.
2. Uncomment the `aws-bedrock/anthropic.claude-3-5-sonnet-...` block in
   `tardigrade_primary.yaml` (priority 1, between Anthropic-direct and OpenAI).
3. `tfy apply -f tardigrade_primary.yaml` to update the VM in place.

## Model ID format

Virtual Models in TF are addressed as `<provider-account-name>/<integration-name>`
— so the primary VM above is called from the OpenAI SDK with
`model="tardigrade-primary/tardigrade-primary"`. That's what's set in
`.env` as `TARDIGRADE_PRIMARY_MODEL`.
