const env = process.env;
const admin = db.getSiblingDB('admin');
if (!admin.auth(env.STATE_MONGO_ROOT_USERNAME, env.STATE_MONGO_ROOT_PASSWORD)) throw new Error('State admin authentication failed');
try { admin.runCommand({replSetGetStatus: 1});
  const result = admin.runCommand({replSetGetStatus: 1});
  if (!result.ok && result.code === 94) rs.initiate({_id:'state-rs',members:[{_id:0,host:'mongo-state:27017'}]});
} catch (error) {
  if (error.code !== 94) throw error;
  rs.initiate({_id:'state-rs',members:[{_id:0,host:'mongo-state:27017'}]});
}
let primary = false;
for (let i=0;i<90;i++) {
  if (admin.runCommand({hello:1}).isWritablePrimary) { primary=true; break; }
  sleep(1000);
}
if (!primary) throw new Error('State replica set did not elect a primary');
for (const [name,user,password] of [
  ['ticketguard_web_state',env.WEB_STATE_MONGO_USERNAME,env.WEB_STATE_MONGO_PASSWORD],
  ['ticketguard_pipeline_state',env.PIPELINE_STATE_MONGO_USERNAME,env.PIPELINE_STATE_MONGO_PASSWORD]
]) {
  const target = db.getSiblingDB(name);
  const existing = target.getUser(user);
  if (!existing) target.createUser({user,pwd:password,roles:[{role:'readWrite',db:name}]});
  else if (existing.roles.length!==1 || existing.roles[0].role!=='readWrite' || existing.roles[0].db!==name)
    throw new Error('Unexpected state user privileges');
}
print('State replica set and isolated users ready');
