const assert=require('node:assert/strict'),{match}=require('./show_request_model.js');
assert.deepEqual(match('Crea una máscara de vidriera'),{kind:'mask',id:'stained-glass'});
assert.deepEqual(match('Un toro volando por la habitación'),{kind:'element',id:'flying-bull'});
assert.deepEqual(match('Pon una plaza de toros al fondo'),{kind:'background',id:'bullring'});
assert.equal(match('A new unicorn flying across the room'),null);
assert.deepEqual(match('Make this cutout jump'),{kind:'motion',id:'jump'});
console.log('PASS: known assets apply, explicit motion edits match, and an unknown new character is not passed off as an existing one');
