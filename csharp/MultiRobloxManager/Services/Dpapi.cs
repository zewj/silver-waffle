using System.Security.Cryptography;
using System.Text;

namespace MultiRobloxManager.Services;

/// <summary>
/// Wraps .NET's built-in DPAPI. Same per-user encryption guarantee as the
/// Python version: only the same Windows user account on the same machine
/// can decrypt. Used to encrypt .ROBLOSECURITY cookies on disk.
/// </summary>
internal static class Dpapi
{
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("multi_roblox");

    public static byte[] Protect(byte[] plaintext)
        => ProtectedData.Protect(plaintext, Entropy, DataProtectionScope.CurrentUser);

    public static byte[] Unprotect(byte[] ciphertext)
        => ProtectedData.Unprotect(ciphertext, Entropy, DataProtectionScope.CurrentUser);

    public static string ProtectString(string plaintext)
        => System.Convert.ToBase64String(Protect(Encoding.UTF8.GetBytes(plaintext)));

    public static string UnprotectString(string ciphertextBase64)
        => Encoding.UTF8.GetString(Unprotect(System.Convert.FromBase64String(ciphertextBase64)));
}
