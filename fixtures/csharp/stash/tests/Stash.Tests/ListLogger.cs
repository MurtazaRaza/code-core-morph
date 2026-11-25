using System;
using System.Collections.Generic;
using Microsoft.Extensions.Logging;

namespace Stash.Tests
{
    /// <summary>What a logger was asked to log.</summary>
    public sealed class Logged
    {
        public Logged(string category, LogLevel level, EventId eventId, string message, Exception exception)
        {
            Category = category;
            Level = level;
            EventId = eventId;
            Message = message;
            Exception = exception;
        }

        public string Category { get; }

        public LogLevel Level { get; }

        public EventId EventId { get; }

        public string Message { get; }

        public Exception Exception { get; }

        public override string ToString()
        {
            return $"{Level} {EventId.Id} {Message}";
        }
    }

    /// <summary>A logger that keeps what it logs, from <see cref="MinLevel"/> up.</summary>
    public class ListLogger : ILogger
    {
        public ListLogger(string category, List<Logged> sink)
        {
            Category = category;
            Sink = sink;
        }

        public string Category { get; }

        public List<Logged> Sink { get; }

        public LogLevel MinLevel { get; set; } = LogLevel.Trace;

        public IDisposable BeginScope<TState>(TState state)
        {
            return null;
        }

        public bool IsEnabled(LogLevel logLevel)
        {
            return logLevel >= MinLevel;
        }

        public void Log<TState>(
            LogLevel logLevel, EventId eventId, TState state, Exception exception,
            Func<TState, Exception, string> formatter)
        {
            if (IsEnabled(logLevel))
            {
                Sink.Add(new Logged(Category, logLevel, eventId, formatter(state, exception), exception));
            }
        }
    }

    public sealed class ListLogger<T> : ListLogger, ILogger<T>
    {
        public ListLogger()
            : base(typeof(T).FullName, new List<Logged>())
        {
        }
    }

    /// <summary>A factory of <see cref="ListLogger"/>s that share one list.</summary>
    public sealed class ListLoggerFactory : ILoggerFactory
    {
        public List<Logged> Sink { get; } = new List<Logged>();

        public List<string> Categories { get; } = new List<string>();

        public LogLevel MinLevel { get; set; } = LogLevel.Trace;

        public void AddProvider(ILoggerProvider provider)
        {
            throw new NotSupportedException();
        }

        public ILogger CreateLogger(string categoryName)
        {
            Categories.Add(categoryName);
            return new ListLogger(categoryName, Sink) { MinLevel = MinLevel };
        }

        public void Dispose()
        {
        }
    }
}
