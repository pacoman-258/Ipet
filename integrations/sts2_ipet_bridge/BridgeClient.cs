using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Channels;

namespace Ipet.Sts2Bridge;

internal sealed class BridgeClient
{
    private const string DefaultBaseUrl = "http://127.0.0.1:8008";
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromMilliseconds(250) };
    private readonly Channel<(string Path, object Payload, int Attempts)> _outbox =
        Channel.CreateBounded<(string, object, int)>(new BoundedChannelOptions(64)
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
            SingleWriter = false,
        });
    private readonly JsonSerializerOptions _json = new(JsonSerializerDefaults.Web)
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };
    private readonly string _sessionId = Guid.NewGuid().ToString("N");
    private readonly string _gameVersion;
    private readonly object _stateLock = new();
    private Timer? _heartbeatTimer;
    private GameSnapshot _snapshot = new(new RunSnapshot(0, 0, 0, string.Empty), null, null, false, true);
    private string _runId = string.Empty;
    private long _sequence;
    private bool _compatible = true;

    internal BridgeClient()
    {
        try
        {
            _gameVersion = Assembly.Load("sts2").GetName().Version?.ToString() ?? string.Empty;
        }
        catch
        {
            _gameVersion = string.Empty;
        }
    }

    internal void Start()
    {
        _ = Task.Run(SendLoopAsync);
        _heartbeatTimer = new Timer(_ => QueueHeartbeat(), null, TimeSpan.Zero, TimeSpan.FromSeconds(3));
    }

    internal void SetCompatibility(bool compatible)
    {
        lock (_stateLock)
        {
            _compatible = compatible;
        }
        QueueHeartbeat();
    }

    internal void BeginRun(GameSnapshot snapshot)
    {
        lock (_stateLock)
        {
            _runId = Guid.NewGuid().ToString("N");
            _snapshot = snapshot;
        }
        QueueHeartbeat();
    }

    internal void UpdateSnapshot(GameSnapshot snapshot)
    {
        lock (_stateLock)
        {
            _snapshot = snapshot;
        }
    }

    internal void EndRun()
    {
        lock (_stateLock)
        {
            _runId = string.Empty;
        }
        QueueHeartbeat();
    }

    internal void Emit(PendingEvent gameEvent)
    {
        string runId;
        lock (_stateLock)
        {
            _snapshot = gameEvent.Snapshot;
            runId = _runId;
        }
        var sequence = Interlocked.Increment(ref _sequence);
        Queue(
            "/api/game/sts2/events",
            new
            {
                schema_version = Protocol.SchemaVersion,
                game = Protocol.GameId,
                session_id = _sessionId,
                run_id = runId,
                game_version = _gameVersion,
                adapter_version = Protocol.AdapterVersion,
                events = new[]
                {
                    new
                    {
                        sequence,
                        occurred_at_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                        kind = gameEvent.Kind,
                        facts = gameEvent.Facts,
                        snapshot = gameEvent.Snapshot.ToWireSnapshot(),
                    },
                },
            });
    }

    private void QueueHeartbeat()
    {
        GameSnapshot snapshot;
        string runId;
        bool compatible;
        lock (_stateLock)
        {
            snapshot = _snapshot;
            runId = _runId;
            compatible = _compatible;
        }
        var player = snapshot.Player;
        Queue(
            "/api/game/sts2/heartbeat",
            new
            {
                schema_version = Protocol.SchemaVersion,
                game = Protocol.GameId,
                session_id = _sessionId,
                run_id = runId,
                game_version = _gameVersion,
                adapter_version = Protocol.AdapterVersion,
                compatible,
                local_player_identified = snapshot.LocalPlayerIdentified,
                event_sequence = Interlocked.Read(ref _sequence),
                run_summary = new
                {
                    act = snapshot.Run.Act,
                    floor = snapshot.Run.Floor,
                    ascension = snapshot.Run.Ascension,
                    character_id = player?.CharacterId ?? string.Empty,
                    hp = player?.Hp ?? 0,
                    max_hp = player?.MaxHp ?? 0,
                    room_type = snapshot.Run.RoomType,
                    multiplayer = snapshot.Multiplayer,
                },
            });
    }

    private void Queue(string path, object payload)
    {
        var attempts = path == "/api/game/sts2/events" ? 3 : 1;
        _outbox.Writer.TryWrite((path, payload, attempts));
    }

    private async Task SendLoopAsync()
    {
        await foreach (var item in _outbox.Reader.ReadAllAsync())
        {
            for (var attempt = 0; attempt < item.Attempts; attempt++)
            {
                var baseUrl = ResolveBaseUrl();
                var token = ResolveToken();
                if (await TryPostAsync(baseUrl, item.Path, item.Payload, token).ConfigureAwait(false))
                {
                    break;
                }

                if (!string.Equals(baseUrl, DefaultBaseUrl, StringComparison.Ordinal)
                    && await TryPostAsync(DefaultBaseUrl, item.Path, item.Payload, token).ConfigureAwait(false))
                {
                    break;
                }

                if (attempt + 1 < item.Attempts)
                {
                    await Task.Delay(TimeSpan.FromMilliseconds(100 * (attempt + 1))).ConfigureAwait(false);
                }
            }
        }
    }

    private async Task<bool> TryPostAsync(string baseUrl, string path, object payload, string? token = null)
    {
        try
        {
            var json = JsonSerializer.Serialize(payload, _json);
            using var request = new HttpRequestMessage(HttpMethod.Post, baseUrl + path)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json"),
            };
            if (!string.IsNullOrEmpty(token))
            {
                request.Headers.Add("X-Ipet-Local-Token", token);
            }
            using var response = await _http.SendAsync(request).ConfigureAwait(false);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            // Ipet is optional. A stopped/restarting backend must never
            // disturb or delay the game thread.
            return false;
        }
    }

    internal static string ResolveToken(string? discoveryPath = null)
    {
        try
        {
            var path = discoveryPath ?? Path.Combine(Path.GetTempPath(), "ipet-game-bridge.json");
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            if (document.RootElement.TryGetProperty("token", out var tokenElement))
            {
                return tokenElement.GetString() ?? string.Empty;
            }

            return string.Empty;
        }
        catch
        {
            return string.Empty;
        }
    }

    internal static string ResolveBaseUrl(string? discoveryPath = null)
    {
        try
        {
            var path = discoveryPath ?? Path.Combine(Path.GetTempPath(), "ipet-game-bridge.json");
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var candidate = document.RootElement.GetProperty("base_url").GetString();
            if (!Uri.TryCreate(candidate, UriKind.Absolute, out var uri)
                || !uri.IsLoopback
                || !string.Equals(uri.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase)
                || !string.IsNullOrEmpty(uri.UserInfo)
                || uri.AbsolutePath != "/"
                || !string.IsNullOrEmpty(uri.Query)
                || !string.IsNullOrEmpty(uri.Fragment))
            {
                return DefaultBaseUrl;
            }

            return uri.GetLeftPart(UriPartial.Authority);
        }
        catch
        {
            return DefaultBaseUrl;
        }
    }
}
