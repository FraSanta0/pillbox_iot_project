// Recupera jsonPayload se esiste, altrimenti crea un oggetto di default per sicurezza
let jsonPayload = msg.jsonPayload || {
    device: "office",
    action: "connection",
    timestamp: new Date().toISOString()
};

// Se la risposta HTTP contiene latitudine e longitudine
if (msg.payload && msg.payload.status === "success") {
    jsonPayload.lat = String(msg.payload.lat);
    jsonPayload.lng = String(msg.payload.lon);
} else {
    // Valori di default se la chiamata HTTP fallisce
    jsonPayload.lat = "0";
    jsonPayload.lng = "0";
}

// Converte in stringa JSON finale per MQTT
msg.payload = JSON.stringify(jsonPayload);
return msg;