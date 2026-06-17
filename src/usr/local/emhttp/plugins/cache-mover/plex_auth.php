<?php
/*
 * Thin proxy for the "Sign in with Plex" flow.
 *
 * Browser JS (cache-mover.page) calls this endpoint; it shells out to
 * plex_auth.py which talks to plex.tv and returns JSON. All user input is
 * charset-filtered and passed via escapeshellarg, and the only network host
 * the Python ever contacts is plex.tv, so there is no SSRF/injection surface.
 */

header('Content-Type: application/json');

$script = '/usr/local/emhttp/plugins/cache-mover/scripts/plex_auth.py';
$action = isset($_REQUEST['action']) ? $_REQUEST['action'] : '';

if (!in_array($action, array('pin', 'poll', 'resources'), true)) {
    echo json_encode(array('error' => 'invalid action'));
    exit;
}

// Plex client identifiers / tokens are alphanumeric with - and _; PIN ids
// are numeric. Strip anything else before it reaches the shell.
$client_id = isset($_REQUEST['client_id'])
    ? preg_replace('/[^A-Za-z0-9_-]/', '', $_REQUEST['client_id']) : '';

$cmd = 'python3 ' . escapeshellarg($script) . ' ' . escapeshellarg($action)
     . ' --client-id ' . escapeshellarg($client_id);

if ($action === 'poll') {
    $id = isset($_REQUEST['id']) ? preg_replace('/[^0-9]/', '', $_REQUEST['id']) : '';
    $cmd .= ' --id ' . escapeshellarg($id);
} elseif ($action === 'resources') {
    $token = isset($_REQUEST['token'])
        ? preg_replace('/[^A-Za-z0-9_-]/', '', $_REQUEST['token']) : '';
    $cmd .= ' --token ' . escapeshellarg($token);
}

$out = shell_exec($cmd . ' 2>/dev/null');
echo ($out !== null && $out !== '') ? $out : json_encode(array('error' => 'plex sign-in helper failed'));
?>
