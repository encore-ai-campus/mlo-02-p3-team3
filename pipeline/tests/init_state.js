for(let i=0;i<60;i++) {
  try { db.adminCommand({ping:1}); break; } catch(error) { if(i===59)throw error; sleep(1000); }
}
try { rs.initiate({_id:'isolated-state',members:[{_id:0,host:'state:27017'}]}); }
catch(error) { if(error.code!==23)throw error; }
let primary=false;
for(let i=0;i<60;i++) { if(db.adminCommand({hello:1}).isWritablePrimary){primary=true;break;} sleep(1000); }
if(!primary)throw Error('Test state replica set unavailable');
