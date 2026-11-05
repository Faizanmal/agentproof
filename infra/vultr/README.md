# Vultr release boundary

No resources are provisioned in Phase 01. Frontend, API and sandbox VM deployment
must use Vultr. Discover model IDs from the real inference `/v1/models` response;
verify the selected model against the completion response. Never substitute another
inference provider. Obtain approval against actual selected plan pricing before
creating paid resources. Keep inference keys in backend/worker configuration only.
