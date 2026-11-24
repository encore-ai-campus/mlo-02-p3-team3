// Run with an existing authorized account on the local system MongoDB.
const target = db.getSiblingDB('ticketguard');
for (const [name, from, fields] of [
    ['network_read_current', 'network_versions', { 'result._id': 1, 'result.data': 1, 'result.publishedAt': 1 }],
    ['network_read_series', 'network_series', { 'result._id': 1, 'result.windowStart': 1, 'result.windowEnd': 1, 'result.buckets': 1 }]
]) {
    const pipeline = [
        {$lookup: {from, localField: 'version', foreignField: '_id', as: 'result'}},
        {$unwind: '$result'},
        {$project: {version: 1, ...fields}}
    ];
    const existing = target.getCollectionInfos({name})[0];
    if (!existing) target.createView(name, 'network_public', pipeline);
    else if (existing.type !== 'view' || existing.options.viewOn !== 'network_public' ||
             JSON.stringify(existing.options.pipeline) !== JSON.stringify(pipeline)) {
        throw new Error('Existing view requires review: ' + name);
    }
}
