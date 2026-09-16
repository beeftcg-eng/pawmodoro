/* launcher.c - Pawmodoro's Windows Desktop-shortcut/taskbar-pin target.
 *
 * This exists purely so the pinned shortcut points at a genuinely
 * distinct, custom-built executable instead of a same-file copy of
 * pythonw.exe. Windows identifies pinned/shortcut targets partly by the
 * executable's own embedded resources (icon, version info), not just its
 * filename - a renamed copy of pythonw.exe still carries Python's own
 * embedded identity, which is why an earlier version of the installer
 * (copying pythonw.exe to Pawmodoro.exe) still showed up mislabeled as
 * an unrelated Python entry (reported: "Idle Python") once pinned. This
 * tiny program shares no bytes with any Python interpreter, so it can't
 * be confused with one.
 *
 * All it does: find its own directory, launch
 * <owndir>\venv\Scripts\pythonw.exe main.py with that directory as the
 * working directory, and exit. The actual icon Windows shows comes from
 * pawmodoro.rc (compiled in at build time), not the Desktop shortcut's
 * IconLocation - so this fixes the pinned tile's identity even before
 * the app launches, not just the running window's.
 */
#include <windows.h>
#include <shlwapi.h>

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, PWSTR pCmdLine, int nCmdShow) {
    wchar_t exePath[MAX_PATH];
    if (GetModuleFileNameW(NULL, exePath, MAX_PATH) == 0) {
        MessageBoxW(NULL, L"Couldn't determine Pawmodoro's install location.", L"Pawmodoro", MB_OK | MB_ICONERROR);
        return 1;
    }
    PathRemoveFileSpecW(exePath); /* exePath is now the install directory */

    wchar_t pythonwPath[MAX_PATH];
    wsprintfW(pythonwPath, L"%s\\venv\\Scripts\\pythonw.exe", exePath);

    if (!PathFileExistsW(pythonwPath)) {
        wchar_t msg[MAX_PATH + 128];
        wsprintfW(msg, L"Couldn't find:\n%s\n\nTry re-running install.bat.", pythonwPath);
        MessageBoxW(NULL, msg, L"Pawmodoro", MB_OK | MB_ICONERROR);
        return 1;
    }

    /* CreateProcessW requires a mutable command-line buffer. */
    wchar_t cmdLine[MAX_PATH + 32];
    wsprintfW(cmdLine, L"\"%s\" main.py", pythonwPath);

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));

    BOOL ok = CreateProcessW(
        pythonwPath, cmdLine,
        NULL, NULL, FALSE,
        CREATE_NO_WINDOW,
        NULL,
        exePath,
        &si, &pi
    );

    if (!ok) {
        wchar_t msg[256];
        wsprintfW(msg, L"Couldn't start Pawmodoro (error code %lu).", GetLastError());
        MessageBoxW(NULL, msg, L"Pawmodoro", MB_OK | MB_ICONERROR);
        return 1;
    }

    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return 0;
}
