using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("AuroraMic")]
[assembly: AssemblyDescription("Floating AuroraMic voice-to-cursor launcher")]
[assembly: AssemblyProduct("AuroraMic")]
[assembly: AssemblyVersion("1.0.0.0")]

namespace AuroraMicLauncher
{
    internal static class Program
    {
        private sealed class PythonCandidate
        {
            public string ExecutablePath;
            public string PrefixArgs;
            public string FullArgsFor(string mainPyPath)
            {
                string quoted = "\"" + mainPyPath + "\"";
                return string.IsNullOrEmpty(PrefixArgs) ? quoted : (PrefixArgs + " " + quoted);
            }
            public string ProbeArgs()
            {
                const string probe = "-c \"import httpx,dotenv,PIL,win32api,numpy,sounddevice\"";
                return string.IsNullOrEmpty(PrefixArgs) ? probe : (PrefixArgs + " " + probe);
            }
        }

        [STAThread]
        private static int Main()
        {
            string projectDir = ResolveProjectDir();
            if (string.IsNullOrEmpty(projectDir))
            {
                MessageBox.Show(
                    "Could not find main.py in or above:\n" + AppDomain.CurrentDomain.BaseDirectory,
                    "AuroraMic Launcher Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }

            string mainPyPath = Path.Combine(projectDir, "main.py");
            PythonCandidate selected = ResolvePythonCandidate(projectDir);
            if (selected == null)
            {
                MessageBox.Show(
                    "Could not locate pythonw.exe or pyw.exe. Please install Python 3.10+ or create a local .venv.",
                    "AuroraMic Launcher Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }

            try
            {
                var startInfo = new ProcessStartInfo
                {
                    FileName = selected.ExecutablePath,
                    Arguments = selected.FullArgsFor(mainPyPath),
                    WorkingDirectory = projectDir,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };

                using (Process process = Process.Start(startInfo))
                {
                    if (process == null)
                    {
                        MessageBox.Show(
                            "Failed to start Python runtime for AuroraMic.",
                            "AuroraMic Launcher Error",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Error
                        );
                        return 1;
                    }
                }

                return 0;
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Failed to launch AuroraMic:\n" + ex.Message,
                    "AuroraMic Launcher Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }
        }

        private static string ResolveProjectDir()
        {
            string[] seeds = new string[]
            {
                AppDomain.CurrentDomain.BaseDirectory,
                Path.GetDirectoryName(Application.ExecutablePath),
            };

            foreach (string seed in seeds)
            {
                if (string.IsNullOrEmpty(seed))
                {
                    continue;
                }

                try
                {
                    string trimmed = seed.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                    var dir = new DirectoryInfo(trimmed);
                    while (dir != null)
                    {
                        if (File.Exists(Path.Combine(dir.FullName, "main.py")))
                        {
                            return dir.FullName;
                        }
                        dir = dir.Parent;
                    }
                }
                catch
                {
                    // Ignore malformed base path candidates.
                }
            }

            return null;
        }

        private static PythonCandidate ResolvePythonCandidate(string projectDir)
        {
            var candidates = new List<PythonCandidate>();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            // 1. Project virtual environments (.venv, then venv)
            string[] venvRelatives = new string[]
            {
                Path.Combine(".venv", Path.Combine("Scripts", "pythonw.exe")),
                Path.Combine("venv", Path.Combine("Scripts", "pythonw.exe")),
            };
            foreach (string rel in venvRelatives)
            {
                AddCandidate(candidates, seen, Path.Combine(projectDir, rel), "");
            }

            // 2. Per-user Python installations under %LOCALAPPDATA%\Programs\Python\Python* (newest first)
            foreach (string pythonw in FindLocalAppDataPythonwCandidates())
            {
                AddCandidate(candidates, seen, pythonw, "");
            }

            // 3. Registered Python installations in HKCU / HKLM PythonCore (newest first)
            foreach (string pythonw in FindRegistryPythonwCandidates())
            {
                AddCandidate(candidates, seen, pythonw, "");
            }

            // 4. pythonw.exe on PATH
            foreach (string pythonw in FindExecutablesOnPath("pythonw.exe"))
            {
                AddCandidate(candidates, seen, pythonw, "");
            }

            // 5. Fallback to Windows Python Launcher pyw.exe -3 main.py
            foreach (string pywExe in FindExecutablesOnPath("pyw.exe"))
            {
                AddCandidate(candidates, seen, pywExe, "-3");
            }
            string winPyw = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.Windows),
                "pyw.exe"
            );
            AddCandidate(candidates, seen, winPyw, "-3");

            if (candidates.Count == 0)
            {
                return null;
            }
            if (candidates.Count == 1)
            {
                return candidates[0];
            }

            foreach (PythonCandidate candidate in candidates)
            {
                if (HasAuroraMicDependencies(candidate))
                {
                    return candidate;
                }
            }

            return candidates[0];
        }

        private static void AddCandidate(
            List<PythonCandidate> list,
            HashSet<string> seen,
            string exePath,
            string prefixArgs
        )
        {
            if (string.IsNullOrEmpty(exePath))
            {
                return;
            }

            try
            {
                if (!File.Exists(exePath))
                {
                    return;
                }
                string full = Path.GetFullPath(exePath);
                string key = full + "|" + (prefixArgs ?? "");
                if (seen.Add(key))
                {
                    list.Add(new PythonCandidate
                    {
                        ExecutablePath = full,
                        PrefixArgs = prefixArgs ?? "",
                    });
                }
            }
            catch
            {
                // Ignore invalid paths.
            }
        }

        private static bool HasAuroraMicDependencies(PythonCandidate candidate)
        {
            try
            {
                var probeInfo = new ProcessStartInfo
                {
                    FileName = candidate.ExecutablePath,
                    Arguments = candidate.ProbeArgs(),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };

                using (Process proc = Process.Start(probeInfo))
                {
                    if (proc == null)
                    {
                        return false;
                    }
                    if (!proc.WaitForExit(8000))
                    {
                        try { proc.Kill(); } catch { }
                        return false;
                    }
                    return proc.ExitCode == 0;
                }
            }
            catch
            {
                return false;
            }
        }

        private static List<string> FindLocalAppDataPythonwCandidates()
        {
            var results = new List<string>();
            try
            {
                string programsPython = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    Path.Combine("Programs", "Python")
                );
                if (!Directory.Exists(programsPython))
                {
                    return results;
                }

                string[] dirs = Directory.GetDirectories(programsPython, "Python*");
                var ranked = new List<KeyValuePair<Version, string>>();
                foreach (string dir in dirs)
                {
                    string pythonw = Path.Combine(dir, "pythonw.exe");
                    if (!File.Exists(pythonw))
                    {
                        continue;
                    }
                    Version ver = ParsePythonFolderVersion(Path.GetFileName(dir));
                    ranked.Add(new KeyValuePair<Version, string>(ver, pythonw));
                }

                ranked.Sort(delegate(KeyValuePair<Version, string> a, KeyValuePair<Version, string> b)
                {
                    int cmp = b.Key.CompareTo(a.Key);
                    if (cmp != 0)
                    {
                        return cmp;
                    }
                    return string.Compare(b.Value, a.Value, StringComparison.OrdinalIgnoreCase);
                });

                foreach (KeyValuePair<Version, string> item in ranked)
                {
                    results.Add(item.Value);
                }
            }
            catch
            {
                // Ignore inaccessible LocalApplicationData Python folder.
            }
            return results;
        }

        private static List<string> FindRegistryPythonwCandidates()
        {
            var ranked = new List<KeyValuePair<Version, string>>();
            RegistryHive[] hives = new RegistryHive[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine };
            RegistryView[] views = new RegistryView[] { RegistryView.Registry64, RegistryView.Registry32 };

            foreach (RegistryHive hive in hives)
            {
                foreach (RegistryView view in views)
                {
                    try
                    {
                        using (RegistryKey baseKey = RegistryKey.OpenBaseKey(hive, view))
                        using (RegistryKey coreKey = baseKey.OpenSubKey(@"SOFTWARE\Python\PythonCore"))
                        {
                            if (coreKey == null)
                            {
                                continue;
                            }

                            foreach (string subName in coreKey.GetSubKeyNames())
                            {
                                try
                                {
                                    using (RegistryKey installKey = coreKey.OpenSubKey(subName + @"\InstallPath"))
                                    {
                                        if (installKey == null)
                                        {
                                            continue;
                                        }

                                        string pythonw = ExtractPythonwFromInstallKey(installKey);
                                        if (string.IsNullOrEmpty(pythonw))
                                        {
                                            continue;
                                        }

                                        Version ver = ParseRegistryVersion(subName);
                                        ranked.Add(new KeyValuePair<Version, string>(ver, pythonw));
                                    }
                                }
                                catch
                                {
                                    // Ignore individual malformed subkeys.
                                }
                            }
                        }
                    }
                    catch
                    {
                        // Ignore inaccessible registry hives or views.
                    }
                }
            }

            ranked.Sort(delegate(KeyValuePair<Version, string> a, KeyValuePair<Version, string> b)
            {
                int cmp = b.Key.CompareTo(a.Key);
                if (cmp != 0)
                {
                    return cmp;
                }
                return string.Compare(b.Value, a.Value, StringComparison.OrdinalIgnoreCase);
            });

            var results = new List<string>();
            foreach (KeyValuePair<Version, string> item in ranked)
            {
                results.Add(item.Value);
            }
            return results;
        }

        private static string ExtractPythonwFromInstallKey(RegistryKey installKey)
        {
            object winExeObj = installKey.GetValue("WindowedExecutablePath");
            if (winExeObj != null)
            {
                string winExe = winExeObj.ToString().Trim().Trim('"');
                if (File.Exists(winExe))
                {
                    return winExe;
                }
            }

            object defaultObj = installKey.GetValue(null);
            if (defaultObj != null)
            {
                string installDir = defaultObj.ToString().Trim().Trim('"');
                if (!string.IsNullOrEmpty(installDir))
                {
                    string candidate = Path.Combine(installDir, "pythonw.exe");
                    if (File.Exists(candidate))
                    {
                        return candidate;
                    }
                }
            }

            object exeObj = installKey.GetValue("ExecutablePath");
            if (exeObj != null)
            {
                string exePath = exeObj.ToString().Trim().Trim('"');
                string exeDir = Path.GetDirectoryName(exePath);
                if (!string.IsNullOrEmpty(exeDir))
                {
                    string candidate = Path.Combine(exeDir, "pythonw.exe");
                    if (File.Exists(candidate))
                    {
                        return candidate;
                    }
                }
            }

            return null;
        }

        private static Version ParsePythonFolderVersion(string folderName)
        {
            if (string.IsNullOrEmpty(folderName))
            {
                return new Version(0, 0);
            }

            string suffix = folderName.StartsWith("Python", StringComparison.OrdinalIgnoreCase)
                ? folderName.Substring("Python".Length)
                : folderName;

            int dashIdx = suffix.IndexOf('-');
            if (dashIdx >= 0)
            {
                suffix = suffix.Substring(0, dashIdx);
            }

            Version dotted;
            if (Version.TryParse(suffix, out dotted))
            {
                return dotted;
            }

            string digits = "";
            foreach (char ch in suffix)
            {
                if (char.IsDigit(ch))
                {
                    digits += ch;
                }
                else
                {
                    break;
                }
            }

            if (digits.Length >= 2)
            {
                int major;
                int minor;
                if (int.TryParse(digits.Substring(0, 1), out major) &&
                    int.TryParse(digits.Substring(1), out minor))
                {
                    return new Version(major, minor);
                }
            }

            int single;
            if (int.TryParse(digits, out single))
            {
                return new Version(single, 0);
            }

            return new Version(0, 0);
        }

        private static Version ParseRegistryVersion(string keyName)
        {
            if (string.IsNullOrEmpty(keyName))
            {
                return new Version(0, 0);
            }

            int dashIdx = keyName.IndexOf('-');
            string clean = dashIdx >= 0 ? keyName.Substring(0, dashIdx) : keyName;
            Version parsed;
            if (Version.TryParse(clean, out parsed))
            {
                return parsed;
            }

            return ParsePythonFolderVersion(clean);
        }

        private static List<string> FindExecutablesOnPath(string fileName)
        {
            var results = new List<string>();
            string pathEnv = Environment.GetEnvironmentVariable("PATH");
            if (string.IsNullOrEmpty(pathEnv))
            {
                return results;
            }

            foreach (string rawEntry in pathEnv.Split(Path.PathSeparator))
            {
                string dir = rawEntry.Trim().Trim('"');
                if (string.IsNullOrEmpty(dir))
                {
                    continue;
                }

                try
                {
                    string candidate = Path.Combine(dir, fileName);
                    if (File.Exists(candidate))
                    {
                        results.Add(candidate);
                    }
                }
                catch
                {
                    // Ignore malformed PATH entries.
                }
            }

            return results;
        }
    }
}
