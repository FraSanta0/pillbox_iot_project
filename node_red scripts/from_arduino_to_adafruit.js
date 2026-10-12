// Recupera la stringa dall'Arduino (msg.payload è "Presa" o "Non presa")
let inputString = JSON.parse(msg.payload);
//node.warn(inputString);
let jsonPayload = {};

// Crea l'oggetto JSON modulare
if (inputString.val === "taken" || inputString.val === "not taken"){
    jsonPayload.action = "REQ";
    jsonPayload.val = inputString.val;
}

if (inputString.command === "connection") {
    jsonPayload.action = "connection";
    //jsonPayload.lat = "44.667772973809235";
    //jsonPayload.lng = "10.474958752675454";
    msg.payload = JSON.stringify(jsonPayload);
    return [msg,null]
}

if (inputString.command === "REF") {
    jsonPayload.action = "REF";
    jsonPayload.status = inputString.status;
}

if (inputString.command === "REQ_STATUS") {
    jsonPayload.action = "REQ_STATUS";
    jsonPayload.status = inputString.status;
}


// Converte l'oggetto in stringa JSON per MQTT
jsonPayload.device = "office";
jsonPayload.timestamp = new Date().toISOString();

// Converte in JSON
msg.payload = JSON.stringify(jsonPayload);
return [null,msg];