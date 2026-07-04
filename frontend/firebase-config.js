/* YatrAI Firebase Configuration and Initialization */
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { 
    getAuth, 
    signInWithEmailAndPassword, 
    createUserWithEmailAndPassword, 
    onAuthStateChanged, 
    signOut, 
    setPersistence, 
    browserLocalPersistence,
    updateProfile
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import { 
    getFirestore, 
    doc, 
    setDoc, 
    getDoc, 
    collection, 
    addDoc, 
    getDocs, 
    query, 
    orderBy,
    serverTimestamp
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";

let app, auth, db;

// Expose initialize function globally so HTML can wait for it
window.initFirebaseConfig = async function() {
    if (app) return; // already initialized
    
    try {
        const response = await fetch('/api/config');
        const configData = await response.json();
        
        const firebaseConfig = {
            apiKey: configData.FIREBASE_API_KEY,
            authDomain: configData.FIREBASE_AUTH_DOMAIN,
            projectId: configData.FIREBASE_PROJECT_ID,
            storageBucket: configData.FIREBASE_STORAGE_BUCKET,
            messagingSenderId: configData.FIREBASE_MESSAGING_SENDER_ID,
            appId: configData.FIREBASE_APP_ID,
            measurementId: configData.FIREBASE_MEASUREMENT_ID
        };

        // Initialize app
        app = initializeApp(firebaseConfig);
        auth = getAuth(app);
        db = getFirestore(app);

        // Apply browser local persistence
        setPersistence(auth, browserLocalPersistence).catch((error) => {
            console.error("Firebase Auth Persistence Error:", error);
        });

        // Expose elements globally for vanilla script integration
        window.firebaseAuth = auth;
        window.firebaseDb = db;
        window.firebaseOps = {
            signInWithEmailAndPassword,
            createUserWithEmailAndPassword,
            onAuthStateChanged,
            signOut,
            updateProfile,
            doc,
            setDoc,
            getDoc,
            collection,
            addDoc,
            getDocs,
            query,
            orderBy,
            serverTimestamp
        };
    } catch (e) {
        console.error("Failed to load Firebase Config:", e);
    }
};
