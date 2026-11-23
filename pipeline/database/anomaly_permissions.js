// Explicit administrator-run preflight/setup. No existing account or role is changed.
const target = db.getSiblingDB(process.env.MONGO_DATABASE || 'ticketguard');
const roleName = 'ticketguardAnomalyReader';
const collections = ['anomaly_public', 'anomaly_health'];
const privileges = collections.map(collection => ({resource: {db: target.getName(), collection}, actions: ['find']}));
const existing = target.getRole(roleName, {showPrivileges: true});
if (existing) {
  const normalize = values => JSON.stringify(values.map(p => ({resource: p.resource, actions: [...p.actions].sort()}))
      .sort((a,b) => a.resource.collection.localeCompare(b.resource.collection)));
  if (existing.roles.length || normalize(existing.privileges) !== normalize(privileges)) {
    throw new Error('Existing anomaly reader role differs; no permissions changed');
  }
}
if (process.env.ANOMALY_APPLY_SETUP === '1') {
  if (!existing) target.createRole({role: roleName, privileges, roles: []});
  print('Anomaly read-only role ready. No account roles changed.');
} else {
  print('Preflight only. Existing DB read role already covers these collections; restricted readers need explicit role assignment by the DB owner.');
}
