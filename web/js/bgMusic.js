/**
 * Copyright © 2026 Monster Spawned Studios
 * https://monsterspawned.studio/
 * All Rights Reserved.
 */

/**
 * Background music for the MSS-Login web application.
 * Files can be loaded from the server (web/assets/bgMusic folder) via the API,
 * or from <audio data-bg-music> elements in the page.
 */

/** Cached list of background music URLs from the server (filesystem). */
let _bgMusicFilesFromServer = null;

/**
 * Load the list of background music files from the server (filesystem).
 * The server lists files in web/assets/bgMusic and returns their URLs.
 * Resolves with the array of URLs; use getBackgroundMusicFiles() after this to read them.
 * @returns {Promise<string[]>}
 */
async function loadBackgroundMusicList() {
    if (_bgMusicFilesFromServer !== null) {
        return _bgMusicFilesFromServer;
    }
    try {
        const response = await fetch("/mss-login/api/bg-music");
        if (response.ok) {
            const data = await response.json();
            if (Array.isArray(data.files) && data.files.length > 0) {
                _bgMusicFilesFromServer = data.files;
                return _bgMusicFilesFromServer;
            }
        }
    } catch (_) {
        // Network or parse error; fall back to DOM below.
    }
    _bgMusicFilesFromServer = [];
    return _bgMusicFilesFromServer;
}

/**
 * Get the list of background music file URLs.
 * Uses the server list (from web/assets/bgMusic) if already loaded via loadBackgroundMusicList();
 * otherwise falls back to <audio data-bg-music> elements on the page.
 * @returns {string[]}
 */
function getBackgroundMusicFiles() {
    if (_bgMusicFilesFromServer !== null && _bgMusicFilesFromServer.length > 0) {
        return _bgMusicFilesFromServer.slice();
    }
    const bgMusicFiles = [];
    const audioElements = document.querySelectorAll("audio[data-bg-music]");
    audioElements.forEach(audio => {
        if (audio.src) bgMusicFiles.push(audio.src);
    });
    return bgMusicFiles;
}


/**
 * Play background music on page load.
 * Loads the list from the server (filesystem) first, then starts the loop.
 */
function playBackgroundMusic() {
    if (!(page.path === "/login" || page.path === "/register" || page.path === "/mfa")) {
        loadBackgroundMusicList().then(() => {
            loopBackgroundMusic(true, true);
        });
    }
}
/**
 * Loop the background music.
 * The player and controls are hidden (audio is not visible on the page).
 */
function loopBackgroundMusic(shuffle = true, repeat = true) {
    let bgMusicFiles = getBackgroundMusicFiles();
    if (shuffle) {
        bgMusicFiles = bgMusicFiles.sort(() => Math.random() - 0.5);
    }
    let index = 0;
    let currentAudio = null;

    function playNextTrack() {
        // Clean up previous audio
        if (currentAudio) {
            currentAudio.pause();
            currentAudio.src = "";
            currentAudio.remove();
        }
        if (bgMusicFiles.length === 0) return;

        currentAudio = document.createElement("audio");
        currentAudio.src = bgMusicFiles[index];
        currentAudio.autoplay = true;
        currentAudio.controls = false;     // no controls
        currentAudio.style.display = "none"; // not visible
        document.body.appendChild(currentAudio);

        // Move to next index for next track
        index++;
        if (index >= bgMusicFiles.length) {
            if (repeat) {
                index = 0;
            } else {
                return; // don't repeat
            }
        }

        // Auto play next when ended
        currentAudio.addEventListener("ended", playNextTrack);
    }
    // Start the loop
    playNextTrack();
}