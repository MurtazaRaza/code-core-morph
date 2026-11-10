// CodeCoreMorph's C# index helper (SPEC 7.2, 11.3, D-65). Compiled by the C# adapter with the
// .NET SDK's own csc, against the SDK's Roslyn (Microsoft.CodeAnalysis), which it loads from
// the SDK at run time; nothing is downloaded for it.
//
// Usage: CsIndexHelper <roslyn dir> <request file>. The request is key=value lines: root=,
// output=, refpack=, then for each project: project=<path>, and after it its source=, define=,
// assets= (obj/project.assets.json) and reference=<project path> lines. Paths of sources and
// projects are relative to root, with forward slashes. The output is JSON lines: sym (a
// declaration), ref (a reference to a project symbol), dep (a file using a type another file
// declares) and problem. With port=1, the source of a port (D-78) also gets type (a type, its
// hierarchy and members), inst (a use of a generic type or method with its type arguments),
// refuse (what a port's first version refuses), throw (an exception a member throws) and test
// (a test method and the TRX names of its cases).

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

public static class CsIndexHelper
{
    public static int Main(string[] args)
    {
        if (args.Length != 2)
        {
            Console.Error.WriteLine("usage: CsIndexHelper <roslyn dir> <request file>");
            return 2;
        }
        string roslyn = args[0];
        AppDomain.CurrentDomain.AssemblyResolve += (sender, e) =>
        {
            string path = Path.Combine(roslyn, new AssemblyName(e.Name).Name + ".dll");
            return File.Exists(path) ? Assembly.LoadFrom(path) : null;
        };
        return Indexer.Run(args[1]);
    }
}

internal sealed class ProjectRequest
{
    public string Path = "";
    public List<string> Sources = new List<string>();
    public List<string> Defines = new List<string>();
    public List<string> References = new List<string>();
    public string Assets = "";
}

internal static class Indexer
{
    internal static readonly SymbolDisplayFormat Signature = new SymbolDisplayFormat(
        globalNamespaceStyle: SymbolDisplayGlobalNamespaceStyle.Omitted,
        typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameAndContainingTypesAndNamespaces,
        genericsOptions: SymbolDisplayGenericsOptions.IncludeTypeParameters
            | SymbolDisplayGenericsOptions.IncludeTypeConstraints,
        memberOptions: SymbolDisplayMemberOptions.IncludeParameters
            | SymbolDisplayMemberOptions.IncludeType
            | SymbolDisplayMemberOptions.IncludeModifiers
            | SymbolDisplayMemberOptions.IncludeAccessibility
            | SymbolDisplayMemberOptions.IncludeRef
            | SymbolDisplayMemberOptions.IncludeConstantValue,
        kindOptions: SymbolDisplayKindOptions.IncludeTypeKeyword
            | SymbolDisplayKindOptions.IncludeMemberKeyword,
        parameterOptions: SymbolDisplayParameterOptions.IncludeType
            | SymbolDisplayParameterOptions.IncludeName
            | SymbolDisplayParameterOptions.IncludeParamsRefOut
            | SymbolDisplayParameterOptions.IncludeDefaultValue
            | SymbolDisplayParameterOptions.IncludeExtensionThis,
        propertyStyle: SymbolDisplayPropertyStyle.ShowReadWriteDescriptor,
        // No IncludeNullableReferenceTypeModifier: annotations change no signature (I-6).
        miscellaneousOptions: SymbolDisplayMiscellaneousOptions.UseSpecialTypes
            | SymbolDisplayMiscellaneousOptions.EscapeKeywordIdentifiers);

    // A property's accessors only its own assembly (or class) can call, as Signature spells them.
    private static readonly string[] HiddenAccessors = new[] { "private protected", "private", "internal" }
        .SelectMany(a => new[] { "get", "set", "init" }.Select(k => " " + a + " " + k + ";"))
        .ToArray();

    private static string WithoutHiddenAccessors(string signature)
    {
        foreach (string accessor in HiddenAccessors)
        {
            signature = signature.Replace(accessor, "");
        }
        return signature;
    }

    // Whether other assemblies see the type: it and each type it is nested in are public or
    // protected.
    private static bool Exposed(INamedTypeSymbol type)
    {
        for (INamedTypeSymbol? t = type; t != null; t = t.ContainingType)
        {
            if (t.DeclaredAccessibility is not (Accessibility.Public or Accessibility.Protected
                or Accessibility.ProtectedOrInternal))
            {
                return false;
            }
        }
        return true;
    }

    internal static readonly SymbolDisplayFormat Qualified = new SymbolDisplayFormat(
        globalNamespaceStyle: SymbolDisplayGlobalNamespaceStyle.Omitted,
        typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameAndContainingTypesAndNamespaces,
        genericsOptions: SymbolDisplayGenericsOptions.None,
        memberOptions: SymbolDisplayMemberOptions.IncludeContainingType);

    private static StreamWriter output = StreamWriter.Null;
    // The reference pack's assemblies, by file name: they win over a package's of the same name.
    private static readonly HashSet<string> FrameworkNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    private static string root = "";
    private static bool port;
    internal static readonly HashSet<string> Known = new HashSet<string>(StringComparer.Ordinal);
    internal static readonly HashSet<string> Emitted = new HashSet<string>(StringComparer.Ordinal);

    [MethodImpl(MethodImplOptions.NoInlining)]
    public static int Run(string requestPath)
    {
        string refpack = "";
        string outputPath = "";
        var projects = new List<ProjectRequest>();
        ProjectRequest? current = null;
        foreach (string raw in File.ReadAllLines(requestPath, Encoding.UTF8))
        {
            int eq = raw.IndexOf('=');
            if (eq <= 0)
            {
                continue;
            }
            string key = raw.Substring(0, eq);
            string value = raw.Substring(eq + 1);
            switch (key)
            {
                case "root": root = value; break;
                case "output": outputPath = value; break;
                case "refpack": refpack = value; break;
                case "port": port = value == "1"; break;
                case "project":
                    current = new ProjectRequest { Path = value };
                    projects.Add(current);
                    break;
                case "source": current?.Sources.Add(value); break;
                case "define": current?.Defines.Add(value); break;
                case "reference": current?.References.Add(value); break;
                case "assets": if (current != null) { current.Assets = value; } break;
            }
        }
        foreach (var p in projects)
        {
            foreach (string s in p.Sources)
            {
                Known.Add(s);
            }
        }
        using (output = new StreamWriter(outputPath, false, new UTF8Encoding(false)))
        {
            var frameworkFiles = Directory.Exists(refpack) ? Directory.GetFiles(refpack, "*.dll") : new string[0];
            var framework = frameworkFiles.Select(f => (MetadataReference)MetadataReference.CreateFromFile(f)).ToList();
            foreach (string file in frameworkFiles)
            {
                FrameworkNames.Add(Path.GetFileName(file));
            }
            if (framework.Count == 0)
            {
                Problem("no framework reference assemblies in " + refpack);
            }
            var built = new Dictionary<string, CSharpCompilation>(StringComparer.OrdinalIgnoreCase);
            foreach (var project in Order(projects))
            {
                var compilation = Compile(project, framework, built);
                built[project.Path] = compilation;
                foreach (var tree in compilation.SyntaxTrees)
                {
                    Walk(compilation, tree);
                    if (port)
                    {
                        PortSource.Walk(compilation, tree, project.Path);
                    }
                }
            }
        }
        return 0;
    }

    // Projects after the projects they reference (a cycle is broken where it is met).
    private static List<ProjectRequest> Order(List<ProjectRequest> projects)
    {
        var byPath = projects.ToDictionary(p => p.Path, StringComparer.OrdinalIgnoreCase);
        var done = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var visiting = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var ordered = new List<ProjectRequest>();
        void Visit(ProjectRequest p)
        {
            if (done.Contains(p.Path) || !visiting.Add(p.Path))
            {
                return;
            }
            foreach (string r in p.References)
            {
                if (byPath.TryGetValue(r, out var other))
                {
                    Visit(other);
                }
            }
            visiting.Remove(p.Path);
            done.Add(p.Path);
            ordered.Add(p);
        }
        foreach (var p in projects)
        {
            Visit(p);
        }
        return ordered;
    }

    private static CSharpCompilation Compile(
        ProjectRequest project,
        List<MetadataReference> framework,
        Dictionary<string, CSharpCompilation> built)
    {
        var options = new CSharpParseOptions(LanguageVersion.Preview, DocumentationMode.None, SourceCodeKind.Regular, project.Defines);
        var trees = new List<SyntaxTree>();
        foreach (string rel in project.Sources)
        {
            string text;
            try
            {
                text = File.ReadAllText(Path.Combine(root, rel), Encoding.UTF8);
            }
            catch (Exception ex)
            {
                Problem(rel + ": " + ex.Message);
                continue;
            }
            var tree = CSharpSyntaxTree.ParseText(text, options, rel);
            var syntaxErrors = tree.GetDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error).Take(1).ToList();
            foreach (var d in syntaxErrors)
            {
                Problem(rel + ":" + (d.Location.GetLineSpan().StartLinePosition.Line + 1) + ": " + d.GetMessage());
            }
            trees.Add(tree);
        }
        var references = new List<MetadataReference>(framework);
        references.AddRange(PackageAssemblies(project.Assets, project.Path));
        foreach (string r in project.References)
        {
            if (built.TryGetValue(r, out var other))
            {
                references.Add(other.ToMetadataReference());
            }
        }
        string name = Path.GetFileNameWithoutExtension(project.Path);
        // As csc does: a package built for netstandard1.x references System.Runtime 4.0, which
        // the desktop comparer unifies with the reference pack's. Without it, attributes from
        // such a package (xUnit 2's, MSTest 2's) bind without their arguments.
        return CSharpCompilation.Create(
            name,
            trees,
            references,
            new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: true)
                .WithAssemblyIdentityComparer(DesktopAssemblyIdentityComparer.Default));
    }

    // The compile assets the restore recorded for the project's first framework.
    // The packages each project restored, by name, with their versions.
    internal static readonly Dictionary<string, Dictionary<string, string>> Packages =
        new Dictionary<string, Dictionary<string, string>>(StringComparer.OrdinalIgnoreCase);

    // The package each package assembly comes from, by assembly name: what a port's types use
    // of a package (D-82).
    internal static readonly Dictionary<string, string> PackageOfAssembly =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

    private static IEnumerable<MetadataReference> PackageAssemblies(string assets, string project)
    {
        var versions = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        Packages[project] = versions;
        var found = new List<MetadataReference>();
        var shipped = new Dictionary<string, (List<string> Assemblies, List<string> Native)>(StringComparer.OrdinalIgnoreCase);
        if (string.IsNullOrEmpty(assets) || !File.Exists(assets))
        {
            return found;
        }
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(assets));
            var top = document.RootElement;
            var folders = top.TryGetProperty("packageFolders", out var f)
                ? f.EnumerateObject().Select(p => p.Name).ToList()
                : new List<string>();
            if (!top.TryGetProperty("targets", out var targets) || !top.TryGetProperty("libraries", out var libraries))
            {
                return found;
            }
            foreach (var target in targets.EnumerateObject())
            {
                foreach (var library in target.Value.EnumerateObject())
                {
                    if (!library.Value.TryGetProperty("type", out var type) || type.GetString() != "package")
                    {
                        continue;
                    }
                    int slash = library.Name.IndexOf('/');
                    if (slash > 0)
                    {
                        versions[library.Name.Substring(0, slash)] = library.Name.Substring(slash + 1);
                    }
                    string packageName = slash > 0 ? library.Name.Substring(0, slash) : library.Name;
                    var record = (Assemblies: new List<string>(), Native: new List<string>());
                    shipped[library.Name] = record;
                    // Its native files, by runtime: what a P/Invoke of it loads (D-82).
                    if (library.Value.TryGetProperty("runtimeTargets", out var runtimeTargets))
                    {
                        foreach (var native in runtimeTargets.EnumerateObject())
                        {
                            if (native.Value.TryGetProperty("assetType", out var assetType) && assetType.GetString() == "native"
                                && native.Value.TryGetProperty("rid", out var rid))
                            {
                                record.Native.Add(rid.GetString() + ":" + Path.GetFileName(native.Name));
                            }
                        }
                    }
                    if (library.Value.TryGetProperty("compile", out var assembliesOf))
                    {
                        foreach (var asset in assembliesOf.EnumerateObject())
                        {
                            if (!asset.Name.EndsWith("/_._", StringComparison.Ordinal))
                            {
                                string assembly = Path.GetFileNameWithoutExtension(asset.Name);
                                record.Assemblies.Add(assembly);
                                PackageOfAssembly[assembly] = packageName;
                            }
                        }
                    }
                    if (!library.Value.TryGetProperty("compile", out var compile)
                        || !libraries.TryGetProperty(library.Name, out var info)
                        || !info.TryGetProperty("path", out var libPath))
                    {
                        continue;
                    }
                    foreach (var asset in compile.EnumerateObject())
                    {
                        // As MSBuild resolves conflicts: the framework's own assembly wins over a
                        // package's (System.Runtime 4.3.0's, which a netstandard1.x package brings),
                        // whose types would otherwise be other types than the framework's.
                        if (asset.Name.EndsWith("/_._", StringComparison.Ordinal)
                            || FrameworkNames.Contains(Path.GetFileName(asset.Name)))
                        {
                            continue;
                        }
                        foreach (string folder in folders)
                        {
                            string full = Path.Combine(folder, libPath.GetString() ?? "", asset.Name);
                            if (File.Exists(full))
                            {
                                found.Add(MetadataReference.CreateFromFile(full));
                                break;
                            }
                        }
                    }
                }
                break; // the first framework
            }
        }
        catch (Exception ex)
        {
            Problem(assets + ": " + ex.Message);
        }
        if (port)
        {
            foreach (var entry in shipped.OrderBy(e => e.Key, StringComparer.Ordinal))
            {
                int slash = entry.Key.IndexOf('/');
                Write(new Dictionary<string, object?>
                {
                    ["t"] = "pkg",
                    ["project"] = project,
                    ["name"] = slash > 0 ? entry.Key.Substring(0, slash) : entry.Key,
                    ["version"] = slash > 0 ? entry.Key.Substring(slash + 1) : "",
                    ["assemblies"] = entry.Value.Assemblies.Distinct(StringComparer.Ordinal).OrderBy(a => a, StringComparer.Ordinal).ToList(),
                    ["native"] = entry.Value.Native.Distinct(StringComparer.Ordinal).OrderBy(n => n, StringComparer.Ordinal).ToList(),
                });
            }
        }
        return found;
    }

    private static void Walk(CSharpCompilation compilation, SyntaxTree tree)
    {
        var model = compilation.GetSemanticModel(tree, ignoreAccessibility: true);
        string file = tree.FilePath;
        var rootNode = tree.GetRoot();
        foreach (var node in rootNode.DescendantNodes())
        {
            switch (node)
            {
                case BaseTypeDeclarationSyntax:
                case DelegateDeclarationSyntax:
                case BaseMethodDeclarationSyntax:
                case BasePropertyDeclarationSyntax:
                case EnumMemberDeclarationSyntax:
                    Declare(model, file, node, model.GetDeclaredSymbol(node));
                    break;
                case VariableDeclaratorSyntax v when v.Parent?.Parent is BaseFieldDeclarationSyntax:
                    Declare(model, file, v.Parent.Parent, model.GetDeclaredSymbol(v));
                    break;
            }
            switch (node)
            {
                case BaseTypeDeclarationSyntax type when type.BaseList != null:
                    foreach (var baseType in type.BaseList.Types)
                    {
                        Reference(model, file, baseType.Type, model.GetSymbolInfo(baseType.Type).Symbol, "inherit");
                    }
                    break;
                case BaseObjectCreationExpressionSyntax creation:
                    Reference(model, file, creation, model.GetSymbolInfo(creation).Symbol, "call");
                    break;
                case SimpleNameSyntax name when !IsDeclarationName(name) && !InBaseList(name):
                    var symbol = model.GetSymbolInfo(name).Symbol ?? model.GetSymbolInfo(name).CandidateSymbols.FirstOrDefault();
                    Reference(model, file, name, symbol, KindOf(symbol));
                    break;
            }
        }
    }

    internal static bool IsDeclarationName(SimpleNameSyntax name) =>
        name.Parent is NameColonSyntax || name.Parent is NameEqualsSyntax;

    private static bool InBaseList(SyntaxNode node) =>
        node.Ancestors().TakeWhile(a => a is not BaseTypeDeclarationSyntax).Any(a => a is BaseListSyntax);

    private static string KindOf(ISymbol? symbol) => symbol switch
    {
        INamedTypeSymbol => "type_use",
        IMethodSymbol => "call",
        _ => "use",
    };

    // "public sealed class Shop.Cart": the display format shows no type's accessibility.
    private static string TypePrefix(INamedTypeSymbol type)
    {
        var words = new List<string>();
        string access = SyntaxFacts.GetText(type.DeclaredAccessibility);
        if (access.Length > 0)
        {
            words.Add(access);
        }
        if (type.IsStatic)
        {
            words.Add("static");
        }
        else if (type.IsAbstract && type.TypeKind == TypeKind.Class)
        {
            words.Add("abstract");
        }
        else if (type.IsSealed && type.TypeKind == TypeKind.Class && !type.IsRecord)
        {
            words.Add("sealed");
        }
        return words.Count > 0 ? string.Join(" ", words) + " " : "";
    }

    private static void Declare(SemanticModel model, string file, SyntaxNode node, ISymbol? symbol)
    {
        if (symbol == null)
        {
            return;
        }
        string? kind = symbol switch
        {
            INamedTypeSymbol t => TypeKindName(t),
            IMethodSymbol m when m.MethodKind == MethodKind.Constructor || m.MethodKind == MethodKind.StaticConstructor => "constructor",
            IMethodSymbol m when m.MethodKind == MethodKind.Destructor => "destructor",
            IMethodSymbol => "method",
            IPropertySymbol => "property",
            IEventSymbol => "event",
            IFieldSymbol f when f.ContainingType?.TypeKind == TypeKind.Enum => "enum_constant",
            IFieldSymbol => "field",
            _ => null,
        };
        string? id = Id(symbol);
        if (kind == null || id == null)
        {
            return;
        }
        var span = node.GetLocation().GetLineSpan();
        string name = symbol is IMethodSymbol { MethodKind: MethodKind.Constructor or MethodKind.StaticConstructor }
            ? symbol.ContainingType.Name
            : symbol.Name;
        string signature = symbol.ToDisplayString(Signature);
        if (symbol is IPropertySymbol)
        {
            // An accessor other code can't call is no part of the signature (I-6): a façade's
            // property keeps `{ get; }` without the private setter the C# had.
            signature = WithoutHiddenAccessors(signature);
        }
        if (symbol is INamedTypeSymbol named)
        {
            signature = TypePrefix(named) + signature;
            var bases = new List<string>();
            if (named.BaseType != null && named.TypeKind == TypeKind.Class
                && named.BaseType.SpecialType != SpecialType.System_Object)
            {
                bases.Add(named.BaseType.ToDisplayString(Signature));
            }
            // Only the interfaces other assemblies see: an internal one (a façade's own) changes
            // no type's API (I-6).
            bases.AddRange(named.Interfaces.Where(Exposed).Select(i => i.ToDisplayString(Signature)));
            if (bases.Count > 0)
            {
                signature += " : " + string.Join(", ", bases);
            }
        }
        string boundary = symbol is IMethodSymbol method && method.GetAttributes().Any(a =>
            a.AttributeClass?.Name is "DllImportAttribute" or "LibraryImportAttribute")
            ? "pinvoke"
            : "none";
        Write(new Dictionary<string, object?>
        {
            ["t"] = "sym",
            ["id"] = id,
            ["name"] = name,
            ["qname"] = symbol.ToDisplayString(Qualified),
            ["kind"] = kind,
            ["file"] = file,
            ["sl"] = span.StartLinePosition.Line + 1,
            ["sc"] = span.StartLinePosition.Character + 1,
            ["el"] = span.EndLinePosition.Line + 1,
            ["ec"] = span.EndLinePosition.Character + 1,
            ["sig"] = signature,
            ["vis"] = Visibility(symbol),
            ["def"] = 1,
            ["b"] = boundary,
        });
        if (symbol is IMethodSymbol { OverriddenMethod: { } overridden } && Id(overridden.OriginalDefinition) is { } target
            && overridden.Locations.Any(l => l.IsInSource && Known.Contains(l.SourceTree?.FilePath ?? "")))
        {
            var where = node.GetLocation().GetLineSpan().StartLinePosition;
            Emit(file, id, target, "override", where.Line + 1, where.Character + 1);
        }
    }

    internal static string? TypeKindName(INamedTypeSymbol t) => t switch
    {
        { IsRecord: true } => "record",
        { TypeKind: TypeKind.Class } => "class",
        { TypeKind: TypeKind.Struct } => "struct",
        { TypeKind: TypeKind.Interface } => "interface",
        { TypeKind: TypeKind.Enum } => "enum",
        { TypeKind: TypeKind.Delegate } => "delegate",
        _ => null,
    };

    private static void Reference(SemanticModel model, string file, SyntaxNode node, ISymbol? symbol, string kind)
    {
        if (symbol == null || symbol is INamespaceSymbol)
        {
            return;
        }
        if (symbol is IMethodSymbol { ReducedFrom: { } reduced })
        {
            symbol = reduced;
        }
        symbol = symbol.OriginalDefinition;
        if (symbol is IAliasSymbol alias)
        {
            symbol = alias.Target;
        }
        var declared = symbol.Locations.Where(l => l.IsInSource && Known.Contains(l.SourceTree?.FilePath ?? "")).ToList();
        if (declared.Count == 0)
        {
            return;
        }
        string? to = Id(symbol);
        if (to == null)
        {
            return;
        }
        var where = node.GetLocation().GetLineSpan().StartLinePosition;
        string? from = Enclosing(model, node);
        Emit(file, from, to, kind, where.Line + 1, where.Character + 1);
        var type = symbol as INamedTypeSymbol ?? symbol.ContainingType;
        if (type == null)
        {
            return;
        }
        foreach (var location in type.Locations.Where(l => l.IsInSource))
        {
            string other = location.SourceTree?.FilePath ?? "";
            if (other != file && Known.Contains(other) && Emitted.Add("dep\0" + file + "\0" + other))
            {
                Write(new Dictionary<string, object?> { ["t"] = "dep", ["from"] = file, ["to"] = other });
            }
        }
    }

    internal static string? Enclosing(SemanticModel model, SyntaxNode node)
    {
        foreach (var ancestor in node.Ancestors())
        {
            if (ancestor is MemberDeclarationSyntax and not NamespaceDeclarationSyntax and not FileScopedNamespaceDeclarationSyntax)
            {
                ISymbol? symbol = ancestor is BaseFieldDeclarationSyntax field
                    ? model.GetDeclaredSymbol(field.Declaration.Variables.First())
                    : model.GetDeclaredSymbol(ancestor);
                if (symbol != null)
                {
                    return Id(symbol);
                }
            }
        }
        return null;
    }

    private static void Emit(string file, string? from, string to, string kind, int line, int col)
    {
        string key = string.Join("\0", "ref", file, line.ToString(), col.ToString(), to, kind);
        if (!Emitted.Add(key))
        {
            return;
        }
        Write(new Dictionary<string, object?>
        {
            ["t"] = "ref",
            ["file"] = file,
            ["from"] = from,
            ["to"] = to,
            ["kind"] = kind,
            ["line"] = line,
            ["col"] = col,
        });
    }

    // csharp:<documentation comment ID without its kind letter>: csharp:Shop.Cart.Add(Shop.Item).
    internal static string? Id(ISymbol symbol)
    {
        string? doc = symbol.GetDocumentationCommentId();
        if (string.IsNullOrEmpty(doc) || doc.Length < 3 || doc[1] != ':')
        {
            return null;
        }
        return "csharp:" + doc.Substring(2);
    }

    private static string Visibility(ISymbol symbol) => symbol.DeclaredAccessibility switch
    {
        Accessibility.Public => "public",
        Accessibility.Protected or Accessibility.ProtectedOrInternal => "protected",
        Accessibility.Private or Accessibility.Internal or Accessibility.ProtectedAndInternal => "private",
        _ => "none",
    };

    private static void Problem(string message)
    {
        Write(new Dictionary<string, object?> { ["t"] = "problem", ["message"] = message });
    }

    internal static void Write(Dictionary<string, object?> row)
    {
        output.WriteLine(JsonSerializer.Serialize(row));
    }
}

// What a port's source records beyond its symbols (D-78): its types with their hierarchies and
// members, the uses of its generics with their type arguments, what the first version of a port
// refuses, the exceptions each member throws, and its test methods with the TRX names of their
// cases.
internal static class PortSource
{
    private static readonly SymbolDisplayFormat TypeArgument = new SymbolDisplayFormat(
        globalNamespaceStyle: SymbolDisplayGlobalNamespaceStyle.Omitted,
        typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameAndContainingTypesAndNamespaces,
        genericsOptions: SymbolDisplayGenericsOptions.IncludeTypeParameters,
        miscellaneousOptions: SymbolDisplayMiscellaneousOptions.UseSpecialTypes);

    // Serializers, dependency injection and composition: behavior driven by attributes and
    // reflection, which the first version refuses.
    private static readonly string[] SerializerNamespaces =
    {
        "System.Runtime.Serialization", "System.Xml.Serialization", "System.Text.Json",
        "Newtonsoft.Json", "System.Web.Script.Serialization",
        "Microsoft.Extensions.DependencyInjection", "System.ComponentModel.Composition",
        "System.Composition",
    };

    // An exception's serialization support (its GetObjectData) is no serializer use.
    private static readonly HashSet<string> SerializationSupport = new HashSet<string>(StringComparer.Ordinal)
    {
        "SerializationInfo", "StreamingContext", "SerializationException", "ISerializable",
    };

    private static readonly HashSet<string> ReflectiveTypeMembers = new HashSet<string>(StringComparer.Ordinal)
    {
        "GetMethod", "GetMethods", "GetProperty", "GetProperties", "GetField", "GetFields",
        "GetMember", "GetMembers", "GetConstructor", "GetConstructors", "GetEvent", "GetEvents",
        "GetInterface", "GetInterfaces", "GetNestedType", "GetNestedTypes", "InvokeMember",
        "MakeGenericType", "GetCustomAttributes", "GetType",
    };

    private static readonly HashSet<string> Described = new HashSet<string>(StringComparer.Ordinal);
    private static readonly HashSet<string> Tested = new HashSet<string>(StringComparer.Ordinal);

    public static void Walk(CSharpCompilation compilation, SyntaxTree tree, string project)
    {
        var model = compilation.GetSemanticModel(tree, ignoreAccessibility: true);
        string file = tree.FilePath;
        RoundTrips.Clear();
        foreach (var body in tree.GetRoot().DescendantNodes().Where(n => n is BaseMethodDeclarationSyntax or AccessorDeclarationSyntax or LocalFunctionStatementSyntax))
        {
            DeepCopy(model, file, body);
        }
        foreach (var node in tree.GetRoot().DescendantNodes())
        {
            if ((node is BaseTypeDeclarationSyntax || node is DelegateDeclarationSyntax)
                && model.GetDeclaredSymbol(node) is INamedTypeSymbol type)
            {
                Describe(type, project);
                Tests(type, project);
            }
            Refusals(model, file, node);
            Throws(model, file, node);
            Uses(model, file, node);
            Native(model, file, node);
            PackageUses(model, file, node);
            Platform(model, file, node);
        }
    }

    private static bool InProject(ISymbol symbol) =>
        symbol.OriginalDefinition.Locations.Any(l => l.IsInSource && Indexer.Known.Contains(l.SourceTree?.FilePath ?? ""));

    private static int Line(SyntaxNode node) => node.GetLocation().GetLineSpan().StartLinePosition.Line + 1;

    // The type a node is in (itself, for a type's declaration).
    private static string? EnclosingType(SemanticModel model, SyntaxNode node)
    {
        foreach (var candidate in node.AncestorsAndSelf())
        {
            if ((candidate is BaseTypeDeclarationSyntax || candidate is DelegateDeclarationSyntax)
                && model.GetDeclaredSymbol(candidate) is INamedTypeSymbol type)
            {
                return Indexer.Id(type);
            }
        }
        return null;
    }

    // ------------------------------------------------------------------ types

    private static void Describe(INamedTypeSymbol type, string project)
    {
        string? id = Indexer.Id(type);
        string? kind = Indexer.TypeKindName(type);
        if (id == null || kind == null || !Described.Add(id))
        {
            return;
        }
        var files = type.DeclaringSyntaxReferences
            .Select(r => r.SyntaxTree.FilePath)
            .Where(f => Indexer.Known.Contains(f))
            .Distinct()
            .OrderBy(f => f, StringComparer.Ordinal)
            .ToList();
        string? baseId = null;
        var interfaces = new List<string>();
        var external = new List<string>();
        if (type.TypeKind == TypeKind.Class && type.BaseType is { } baseType
            && baseType.SpecialType != SpecialType.System_Object)
        {
            if (InProject(baseType) && Indexer.Id(baseType.OriginalDefinition) is { } known)
            {
                baseId = known;
            }
            else
            {
                external.Add(baseType.ToDisplayString(TypeArgument));
            }
        }
        foreach (var implemented in type.Interfaces)
        {
            if (InProject(implemented) && Indexer.Id(implemented.OriginalDefinition) is { } known)
            {
                interfaces.Add(known);
            }
            else
            {
                external.Add(implemented.ToDisplayString(TypeArgument));
            }
        }
        var members = new List<string>();
        var virtuals = new List<string>();
        var fields = new List<string>();
        var statics = new List<string>();
        foreach (var member in type.GetMembers())
        {
            if (member is INamedTypeSymbol || (member.IsImplicitlyDeclared && member is not IFieldSymbol))
            {
                continue;
            }
            if (member is IMethodSymbol { MethodKind: MethodKind.PropertyGet or MethodKind.PropertySet
                or MethodKind.EventAdd or MethodKind.EventRemove or MethodKind.EventRaise })
            {
                continue;
            }
            string? memberId = Indexer.Id(member);
            if (memberId == null)
            {
                continue;
            }
            if (!member.IsImplicitlyDeclared && member.DeclaredAccessibility is Accessibility.Public
                or Accessibility.Protected or Accessibility.ProtectedOrInternal)
            {
                members.Add(memberId);
            }
            if (type.TypeKind == TypeKind.Class && !member.IsSealed
                && (member.IsVirtual || member.IsAbstract || member.IsOverride))
            {
                virtuals.Add(memberId);
            }
            if (member is IFieldSymbol field && !field.IsConst)
            {
                if (!field.IsStatic && !field.IsImplicitlyDeclared && field.DeclaredAccessibility != Accessibility.Private)
                {
                    fields.Add(field.Name);
                }
                if (field.IsStatic && !(field.IsReadOnly && Immutable(field.Type))
                    && Indexer.Id(field.AssociatedSymbol ?? field) is { } state && !statics.Contains(state))
                {
                    statics.Add(state);
                }
            }
        }
        Indexer.Write(new Dictionary<string, object?>
        {
            ["t"] = "type",
            ["id"] = id,
            ["name"] = type.ToDisplayString(Indexer.Qualified),
            ["kind"] = kind,
            ["files"] = files,
            ["project"] = project,
            ["outer"] = type.ContainingType != null ? Indexer.Id(type.ContainingType) : null,
            ["base"] = baseId,
            ["interfaces"] = interfaces,
            ["external"] = external,
            ["abstract"] = type.TypeKind == TypeKind.Class && type.IsAbstract && !type.IsStatic,
            ["sealed"] = type.TypeKind == TypeKind.Class && type.IsSealed && !type.IsStatic,
            ["static"] = type.IsStatic,
            ["tparams"] = type.TypeParameters.Select(p => p.Name).ToList(),
            ["members"] = members,
            ["virtual"] = virtuals,
            ["fields"] = fields,
            ["statics"] = statics,
            ["native_only"] = NativeOnly(type),
        });
    }

    // A type that only declares native functions (P/Invoke externs), with the constants and the
    // plain structs they take: the new code calls the native library itself, so it needs no
    // port (D-82).
    private static bool NativeOnly(INamedTypeSymbol type) =>
        HasExtern(type) && type.GetMembers().All(m => m switch
        {
            INamedTypeSymbol nested => NativeOnly(nested) || NativeStruct(nested) || ConstantsOnly(nested),
            IMethodSymbol { IsExtern: true } => true,
            IMethodSymbol { IsImplicitlyDeclared: true, MethodKind: MethodKind.Constructor } => true,
            IFieldSymbol { IsConst: true } => true,
            _ => false,
        });

    private static bool HasExtern(INamedTypeSymbol type) =>
        type.GetMembers().Any(m => m is IMethodSymbol { IsExtern: true } || (m is INamedTypeSymbol nested && HasExtern(nested)));

    private static bool NativeStruct(INamedTypeSymbol type) =>
        type.TypeKind == TypeKind.Struct && type.GetMembers().All(m =>
            m is IFieldSymbol { IsStatic: false } || m.IsImplicitlyDeclared);

    private static bool ConstantsOnly(INamedTypeSymbol type) =>
        type.TypeKind == TypeKind.Class && type.GetMembers().All(m =>
            m is IFieldSymbol { IsConst: true } || m.IsImplicitlyDeclared);

    // A value no code can change: a readonly field of one of these is no state.
    private static bool Immutable(ITypeSymbol type) =>
        type.SpecialType != SpecialType.None || type.TypeKind == TypeKind.Enum;

    // ------------------------------------------------------------------ refusals

    private static void Refusals(SemanticModel model, string file, SyntaxNode node)
    {
        if (RoundTrips.Contains(node))
        {
            return; // a serializer's round trip in memory: a deep copy (D-82)
        }
        switch (node)
        {
            case MemberDeclarationSyntax member when member is not BaseTypeDeclarationSyntax || member.Modifiers.Any(SyntaxKind.UnsafeKeyword):
                string name = model.GetDeclaredSymbol(member)?.Name ?? "";
                if (member.Modifiers.Any(SyntaxKind.AsyncKeyword))
                {
                    Refuse(model, file, node, "async", "async " + name);
                }
                if (member.Modifiers.Any(SyntaxKind.UnsafeKeyword))
                {
                    Notice(model, file, node, "unsafe", "unsafe " + name);
                }
                break;
            case LocalFunctionStatementSyntax local:
                if (local.Modifiers.Any(SyntaxKind.AsyncKeyword))
                {
                    Refuse(model, file, node, "async", "async " + local.Identifier.ValueText);
                }
                if (local.Modifiers.Any(SyntaxKind.UnsafeKeyword))
                {
                    Notice(model, file, node, "unsafe", "unsafe " + local.Identifier.ValueText);
                }
                break;
            case AnonymousFunctionExpressionSyntax lambda:
                if (lambda.AsyncKeyword.IsKind(SyntaxKind.AsyncKeyword))
                {
                    Refuse(model, file, node, "async", "an async lambda");
                }
                if (model.GetTypeInfo(lambda).ConvertedType is INamedTypeSymbol { Name: "Expression", Arity: 1 } tree
                    && tree.ContainingNamespace.ToDisplayString() == "System.Linq.Expressions")
                {
                    Refuse(model, file, node, "expression_tree", "a lambda as " + tree.ToDisplayString(TypeArgument));
                }
                break;
            case UnsafeStatementSyntax:
                Notice(model, file, node, "unsafe", "an unsafe block");
                break;
            case FixedStatementSyntax:
                Notice(model, file, node, "unsafe", "a fixed statement");
                break;
            case PointerTypeSyntax pointer:
                Notice(model, file, node, "unsafe", "the pointer type " + pointer);
                break;
            case FunctionPointerTypeSyntax:
                Notice(model, file, node, "unsafe", "a function pointer");
                break;
            case IdentifierNameSyntax word when word.Identifier.ValueText == "dynamic"
                && (model.GetTypeInfo(word).Type?.TypeKind == TypeKind.Dynamic || model.GetSymbolInfo(word).Symbol is IDynamicTypeSymbol):
                Refuse(model, file, node, "dynamic", "dynamic");
                break;
            case InvocationExpressionSyntax call when model.GetSymbolInfo(call).Symbol is IMethodSymbol method:
                var owner = method.ContainingType;
                string space = owner?.ContainingNamespace?.ToDisplayString() ?? "";
                bool reflective = space == "System.Reflection" || space.StartsWith("System.Reflection.", StringComparison.Ordinal)
                    || (space == "System" && owner?.Name == "Activator")
                    || (space == "System" && owner?.Name == "Type" && ReflectiveTypeMembers.Contains(method.Name))
                    || (space == "System" && owner?.Name == "Attribute" && method.Name.StartsWith("GetCustomAttribute", StringComparison.Ordinal));
                if (owner != null && (reflective || Serializer(owner)))
                {
                    Refuse(model, file, node, reflective ? "reflection" : "serialization",
                        owner.ToDisplayString(Indexer.Qualified) + "." + method.Name);
                }
                break;
            case ObjectCreationExpressionSyntax creation when model.GetSymbolInfo(creation).Symbol is IMethodSymbol { ContainingType: { } created } && Serializer(created):
                Refuse(model, file, node, "serialization", "new " + created.ToDisplayString(Indexer.Qualified));
                break;
            case AttributeSyntax attribute when model.GetSymbolInfo(attribute).Symbol is IMethodSymbol { ContainingType: { } driving } && Serializer(driving):
                Refuse(model, file, node, "serialization", "[" + driving.ToDisplayString(Indexer.Qualified) + "]");
                break;
        }
    }

    private static bool Serializer(INamedTypeSymbol type)
    {
        if (SerializationSupport.Contains(type.Name))
        {
            return false;
        }
        string space = type.ContainingNamespace?.ToDisplayString() ?? "";
        return SerializerNamespaces.Any(n => space == n || space.StartsWith(n + ".", StringComparison.Ordinal));
    }

    private static void Refuse(SemanticModel model, string file, SyntaxNode node, string what, string detail)
    {
        string? type = EnclosingType(model, node);
        if (type == null)
        {
            return;
        }
        int line = Line(node);
        if (!Indexer.Emitted.Add(string.Join("\0", "refuse", what, file, line.ToString(), detail)))
        {
            return;
        }
        string? member = node is MemberDeclarationSyntax and not BaseTypeDeclarationSyntax && model.GetDeclaredSymbol(node) is { } declared
            ? Indexer.Id(declared)
            : Indexer.Enclosing(model, node);
        Indexer.Write(new Dictionary<string, object?>
        {
            ["t"] = "refuse",
            ["what"] = what,
            ["detail"] = detail,
            ["type"] = type,
            ["member"] = member,
            ["file"] = file,
            ["line"] = line,
        });
    }

    // What a unit's note tells about a site that the port takes over without refusing it
    // (D-82): unsafe code, a deep copy through a serializer, code that asks which platform it
    // runs on or loads native code itself.
    private static void Notice(SemanticModel model, string file, SyntaxNode node, string what, string detail)
    {
        string? type = EnclosingType(model, node);
        if (type == null)
        {
            return;
        }
        int line = Line(node);
        if (!Indexer.Emitted.Add(string.Join("\0", "notice", what, file, line.ToString(), detail)))
        {
            return;
        }
        string? member = node is MemberDeclarationSyntax and not BaseTypeDeclarationSyntax && model.GetDeclaredSymbol(node) is { } declared
            ? Indexer.Id(declared)
            : Indexer.Enclosing(model, node);
        Indexer.Write(new Dictionary<string, object?>
        {
            ["t"] = "notice",
            ["what"] = what,
            ["detail"] = detail,
            ["type"] = type,
            ["member"] = member,
            ["file"] = file,
            ["line"] = line,
        });
    }

    // ------------------------------------------------------------------ deep copies (D-82)

    // The serializer nodes of the round trips in memory found in the current tree: a deep copy,
    // which the port makes by copying the object graph rather than refusing it.
    private static readonly HashSet<SyntaxNode> RoundTrips = new HashSet<SyntaxNode>();

    private static readonly HashSet<string> SerializationCallbacks = new HashSet<string>(StringComparer.Ordinal)
    {
        "OnSerializingAttribute", "OnSerializedAttribute", "OnDeserializingAttribute", "OnDeserializedAttribute",
    };

    // A formatter (an IFormatter, BinaryFormatter's kind) made in the body serializes an object
    // into a MemoryStream made in the body and deserializes it back from the same stream: the
    // object graph copied, its bytes never seen outside. Not when a type of the graph decides its
    // own serialization (ISerializable, the serialization callbacks): that is the type's code.
    private static void DeepCopy(SemanticModel model, string file, SyntaxNode body)
    {
        var nodes = body.DescendantNodes().ToList();
        var formatters = nodes.OfType<ObjectCreationExpressionSyntax>()
            .Where(c => model.GetSymbolInfo(c).Symbol is IMethodSymbol { ContainingType: { } t } && Formatter(t))
            .ToList();
        if (formatters.Count == 0)
        {
            return;
        }
        InvocationExpressionSyntax? serialize = null, deserialize = null;
        ISymbol? written = null, read = null;
        ExpressionSyntax? copied = null;
        foreach (var call in nodes.OfType<InvocationExpressionSyntax>())
        {
            if (model.GetSymbolInfo(call).Symbol is not IMethodSymbol { ContainingType: { } owner } method || !Formatter(owner))
            {
                continue;
            }
            var arguments = call.ArgumentList.Arguments;
            if (method.Name == "Serialize" && arguments.Count == 2 && serialize == null)
            {
                serialize = call;
                written = model.GetSymbolInfo(arguments[0].Expression).Symbol;
                copied = arguments[1].Expression;
            }
            else if (method.Name == "Deserialize" && arguments.Count == 1 && deserialize == null)
            {
                deserialize = call;
                read = model.GetSymbolInfo(arguments[0].Expression).Symbol;
            }
            else
            {
                return; // another use of the formatter: not a plain round trip
            }
        }
        if (serialize == null || deserialize == null || copied == null || written is not ILocalSymbol stream
            || !SymbolEqualityComparer.Default.Equals(written, read)
            || stream.Type.ToDisplayString() != "System.IO.MemoryStream"
            || !nodes.OfType<ObjectCreationExpressionSyntax>().Any(c => model.GetTypeInfo(c).Type?.ToDisplayString() == "System.IO.MemoryStream"))
        {
            return;
        }
        var graph = Graph(model.GetTypeInfo(copied).Type);
        if (graph.Any(t => t.AllInterfaces.Any(i => i.ToDisplayString() == "System.Runtime.Serialization.ISerializable")
            || t.GetMembers().OfType<IMethodSymbol>().Any(m => m.GetAttributes().Any(a => SerializationCallbacks.Contains(a.AttributeClass?.Name ?? "")))))
        {
            return;
        }
        RoundTrips.UnionWith(formatters);
        RoundTrips.Add(serialize);
        RoundTrips.Add(deserialize);
        var reset = graph.SelectMany(t => t.GetMembers().OfType<IFieldSymbol>()
                .Where(f => !f.IsStatic && f.GetAttributes().Any(a => a.AttributeClass?.Name == "NonSerializedAttribute"))
                .Select(f => t.Name + "." + f.Name))
            .OrderBy(n => n, StringComparer.Ordinal)
            .ToList();
        string what = model.GetTypeInfo(copied).Type?.ToDisplayString(Indexer.Qualified) ?? "the object";
        string formatter = model.GetTypeInfo(formatters[0]).Type?.Name ?? "a formatter";
        string detail = "a deep copy of " + what + " through a " + formatter + " round trip in memory";
        if (reset.Count > 0)
        {
            detail += " ([NonSerialized], so reset in the copy: " + string.Join(", ", reset) + ")";
        }
        Notice(model, file, serialize, "deep_copy", detail);
    }

    private static bool Formatter(INamedTypeSymbol type) =>
        type.AllInterfaces.Any(i => i.ToDisplayString() == "System.Runtime.Serialization.IFormatter")
        || type.ToDisplayString() == "System.Runtime.Serialization.IFormatter";

    // The project's types an object of this type reaches through instance fields (type arguments
    // of a collection included).
    private static List<INamedTypeSymbol> Graph(ITypeSymbol? start)
    {
        var found = new List<INamedTypeSymbol>();
        var queue = new Queue<ITypeSymbol>();
        if (start != null)
        {
            queue.Enqueue(start);
        }
        var seen = new HashSet<ITypeSymbol>(SymbolEqualityComparer.Default);
        while (queue.Count > 0)
        {
            var type = queue.Dequeue();
            if (!seen.Add(type))
            {
                continue;
            }
            if (type is IArrayTypeSymbol array)
            {
                queue.Enqueue(array.ElementType);
                continue;
            }
            if (type is not INamedTypeSymbol named)
            {
                continue;
            }
            foreach (var argument in named.TypeArguments)
            {
                queue.Enqueue(argument);
            }
            if (!InProject(named))
            {
                continue;
            }
            found.Add(named);
            foreach (var field in named.GetMembers().OfType<IFieldSymbol>().Where(f => !f.IsStatic))
            {
                queue.Enqueue(field.Type);
            }
            if (named.BaseType != null)
            {
                queue.Enqueue(named.BaseType);
            }
        }
        return found;
    }

    // ------------------------------------------------------------------ native code (D-82)

    // A P/Invoke declaration: the library it names (its constant resolved), the entry point.
    private static void Native(SemanticModel model, string file, SyntaxNode node)
    {
        if (node is not MethodDeclarationSyntax declaration || model.GetDeclaredSymbol(declaration) is not IMethodSymbol method
            || !method.IsExtern)
        {
            return;
        }
        var import = method.GetAttributes().FirstOrDefault(a => a.AttributeClass?.Name is "DllImportAttribute" or "LibraryImportAttribute");
        string? type = EnclosingType(model, node);
        string? id = Indexer.Id(method);
        if (import == null || type == null || id == null)
        {
            return;
        }
        string library = import.ConstructorArguments.Length > 0 ? import.ConstructorArguments[0].Value as string ?? "" : "";
        string entry = import.NamedArguments.FirstOrDefault(a => a.Key == "EntryPoint").Value.Value as string ?? method.Name;
        Indexer.Write(new Dictionary<string, object?>
        {
            ["t"] = "native",
            ["library"] = library,
            ["entry"] = entry,
            ["type"] = type,
            ["member"] = id,
            ["sig"] = method.ToDisplayString(Indexer.Signature),
            ["file"] = file,
            ["line"] = Line(node),
        });
    }

    // A package's API that a type uses, once per symbol and type: what the new code needs a
    // counterpart of (D-82).
    private static void PackageUses(SemanticModel model, string file, SyntaxNode node)
    {
        if (node is not SimpleNameSyntax name || Indexer.IsDeclarationName(name))
        {
            return;
        }
        var info = model.GetSymbolInfo(name);
        var symbol = info.Symbol ?? (info.CandidateSymbols.Length == 1 ? info.CandidateSymbols[0] : null);
        if (symbol == null || symbol is INamespaceSymbol || symbol.ContainingAssembly == null
            || !Indexer.PackageOfAssembly.TryGetValue(symbol.ContainingAssembly.Name, out var package))
        {
            return;
        }
        string? type = EnclosingType(model, node);
        string shown = symbol is INamedTypeSymbol ? symbol.ToDisplayString(Indexer.Qualified)
            : (symbol.ContainingType?.ToDisplayString(Indexer.Qualified) ?? "") + "." + symbol.Name;
        if (type == null || !Indexer.Emitted.Add(string.Join("\0", "pkguse", type, package, shown)))
        {
            return;
        }
        Indexer.Write(new Dictionary<string, object?>
        {
            ["t"] = "pkguse",
            ["package"] = package,
            ["symbol"] = shown,
            ["type"] = type,
            ["member"] = Indexer.Enclosing(model, node),
            ["file"] = file,
            ["line"] = Line(node),
        });
    }

    private static readonly HashSet<string> PlatformTypes = new HashSet<string>(StringComparer.Ordinal)
    {
        "System.Runtime.InteropServices.RuntimeInformation", "System.Runtime.InteropServices.NativeLibrary",
        "System.OperatingSystem",
    };

    // Code that asks which platform it runs on, or loads native code itself: the new code is
    // built for its platform and links its libraries (D-82).
    private static void Platform(SemanticModel model, string file, SyntaxNode node)
    {
        if (node is not MemberAccessExpressionSyntax access)
        {
            return;
        }
        var symbol = model.GetSymbolInfo(access).Symbol;
        string owner = symbol?.ContainingType?.ToDisplayString() ?? "";
        bool platform = PlatformTypes.Contains(owner)
            || (owner == "System.Environment" && symbol?.Name == "OSVersion");
        if (symbol != null && platform)
        {
            Notice(model, file, node, "platform", owner + "." + symbol.Name);
        }
    }

    // ------------------------------------------------------------------ exceptions

    private static void Throws(SemanticModel model, string file, SyntaxNode node)
    {
        ExpressionSyntax? thrown = node switch
        {
            ThrowStatementSyntax statement => statement.Expression,
            ThrowExpressionSyntax expression => expression.Expression,
            _ => null,
        };
        if (thrown == null)
        {
            return; // a rethrow: the exception it caught
        }
        var exception = model.GetTypeInfo(thrown).Type;
        string? type = EnclosingType(model, node);
        if (exception == null || exception.TypeKind == TypeKind.Error || type == null)
        {
            return;
        }
        Indexer.Write(new Dictionary<string, object?>
        {
            ["t"] = "throw",
            ["exception"] = exception.ToDisplayString(Indexer.Qualified),
            ["type"] = type,
            ["member"] = Indexer.Enclosing(model, node),
            ["file"] = file,
            ["line"] = Line(node),
        });
    }

    // ------------------------------------------------------------------ generics in use

    private static void Uses(SemanticModel model, string file, SyntaxNode node)
    {
        if (node is not SimpleNameSyntax name || Indexer.IsDeclarationName(name))
        {
            return;
        }
        var info = model.GetSymbolInfo(name);
        var symbol = info.Symbol ?? (info.CandidateSymbols.Length == 1 ? info.CandidateSymbols[0] : null);
        switch (symbol)
        {
            case INamedTypeSymbol type:
                UseType(model, file, name, type);
                break;
            case IMethodSymbol method:
                var full = method.MethodKind == MethodKind.ReducedExtension ? method.GetConstructedReducedFrom() ?? method : method;
                if (full.IsGenericMethod && !SymbolEqualityComparer.Default.Equals(full, full.OriginalDefinition)
                    && InProject(full))
                {
                    Use(model, file, name, full.OriginalDefinition, full.TypeArguments);
                }
                if (full.ContainingType != null)
                {
                    UseType(model, file, name, full.ContainingType);
                }
                break;
            case IPropertySymbol or IFieldSymbol or IEventSymbol when symbol.ContainingType != null:
                UseType(model, file, name, symbol.ContainingType);
                break;
        }
    }

    // A constructed generic type of the project, and the generic types it is nested in.
    private static void UseType(SemanticModel model, string file, SyntaxNode node, INamedTypeSymbol type)
    {
        for (var t = type; t != null; t = t.ContainingType)
        {
            if (t.IsGenericType && !t.IsUnboundGenericType && !SymbolEqualityComparer.Default.Equals(t, t.OriginalDefinition)
                && InProject(t))
            {
                Use(model, file, node, t.OriginalDefinition, t.TypeArguments);
            }
        }
    }

    private static void Use(SemanticModel model, string file, SyntaxNode node, ISymbol generic, IEnumerable<ITypeSymbol> arguments)
    {
        string? of = Indexer.Id(generic);
        if (of == null)
        {
            return;
        }
        var shown = arguments.Select(a => a.ToDisplayString(TypeArgument)).ToList();
        int line = Line(node);
        if (!Indexer.Emitted.Add(string.Join("\0", "inst", of, string.Join(",", shown), file, line.ToString())))
        {
            return;
        }
        Indexer.Write(new Dictionary<string, object?>
        {
            ["t"] = "inst",
            ["of"] = of,
            ["args"] = shown,
            ["open"] = arguments.Any(Open),
            ["member"] = Indexer.Enclosing(model, node),
            ["file"] = file,
            ["line"] = line,
        });
    }

    private static bool Open(ITypeSymbol type) => type switch
    {
        ITypeParameterSymbol => true,
        IArrayTypeSymbol array => Open(array.ElementType),
        IPointerTypeSymbol pointer => Open(pointer.PointedAtType),
        INamedTypeSymbol named => named.TypeArguments.Any(Open) || (named.ContainingType != null && Open(named.ContainingType)),
        _ => false,
    };

    // ------------------------------------------------------------------ tests

    // The class as a TRX report names it: its namespace, then the classes it is nested in, by "+".
    private static string TrxClass(INamedTypeSymbol type)
    {
        var names = new List<string>();
        for (var t = type; t != null; t = t.ContainingType)
        {
            names.Insert(0, t.Name);
        }
        string space = type.ContainingNamespace is { IsGlobalNamespace: false } ns ? ns.ToDisplayString() + "." : "";
        return space + string.Join("+", names);
    }

    private static bool Is(INamedTypeSymbol? type, string space, params string[] names)
    {
        for (var t = type; t != null; t = t.BaseType)
        {
            if (names.Contains(t.Name) && t.ContainingNamespace?.ToDisplayString() == space)
            {
                return true;
            }
        }
        return false;
    }

    private static string? Framework(INamedTypeSymbol? attribute)
    {
        if (Is(attribute, "NUnit.Framework", "TestAttribute", "TestCaseAttribute", "TestCaseSourceAttribute", "TheoryAttribute"))
        {
            return "nunit";
        }
        if (Is(attribute, "Xunit", "FactAttribute"))
        {
            return "xunit";
        }
        if (Is(attribute, "Microsoft.VisualStudio.TestTools.UnitTesting", "TestMethodAttribute"))
        {
            return "mstest";
        }
        return null;
    }

    // MSTest.TestFramework's package version in the project, or null.
    private static Version? MSTestVersion(string project)
    {
        return Indexer.Packages.TryGetValue(project, out var versions)
            && versions.TryGetValue("MSTest.TestFramework", out var text)
            && Version.TryParse(text.Split('-')[0], out var version) ? version : null;
    }

    private static void Tests(INamedTypeSymbol type, string project)
    {
        if (type.TypeKind != TypeKind.Class || type.IsAbstract || type.IsStatic)
        {
            return; // an abstract class's tests run as its derived classes'
        }
        string? id = Indexer.Id(type);
        if (id == null || !Tested.Add(id))
        {
            return;
        }
        bool generic = false;
        for (var t = type; t != null; t = t.ContainingType)
        {
            generic |= t.IsGenericType;
        }
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (var t = type; t != null && InProject(t); t = t.BaseType)
        {
            foreach (var method in t.GetMembers().OfType<IMethodSymbol>())
            {
                if (method.MethodKind != MethodKind.Ordinary)
                {
                    continue;
                }
                var attributes = method.GetAttributes();
                string? framework = attributes.Select(a => Framework(a.AttributeClass)).FirstOrDefault(f => f != null);
                if (framework == null || !seen.Add(method.Name))
                {
                    continue;
                }
                string file = method.Locations.FirstOrDefault(l => l.IsInSource)?.SourceTree?.FilePath ?? "";
                if (!Indexer.Known.Contains(file))
                {
                    continue;
                }
                // A generic fixture, or NUnit's fixture with arguments, adds to the class's name.
                bool exact = !generic && !method.IsGenericMethod && !type.GetAttributes().Any(a =>
                    Is(a.AttributeClass, "NUnit.Framework", "TestFixtureAttribute") && a.ConstructorArguments.Length > 0);
                var cases = framework switch
                {
                    "nunit" => TestNames.NUnit(method, attributes, ref exact),
                    "xunit" => TestNames.Xunit(method, attributes, ref exact),
                    _ => TestNames.MSTest(method, attributes, MSTestVersion(project), ref exact),
                };
                Indexer.Write(new Dictionary<string, object?>
                {
                    ["t"] = "test",
                    ["file"] = file,
                    ["project"] = project,
                    ["cls"] = TrxClass(type),
                    ["method"] = method.Name,
                    ["fw"] = framework,
                    ["cases"] = cases,
                    ["exact"] = exact,
                });
            }
        }
    }
}

// The names each framework gives a test's cases in its TRX report, from the test's attributes:
// the method's name, and a parameterized case's arguments as the framework shows them. A case
// the source can't tell (a data source, an argument shown its own way) makes the list inexact.
internal static class TestNames
{
    private static bool Named(AttributeData attribute, string space, string name) =>
        attribute.AttributeClass is { } c && c.Name == name && c.ContainingNamespace?.ToDisplayString() == space;

    private static string? NamedString(AttributeData attribute, string name)
    {
        foreach (var pair in attribute.NamedArguments)
        {
            if (pair.Key == name && pair.Value.Value is string text)
            {
                return text;
            }
        }
        return null;
    }

    // A data attribute's values: its constructor's arguments, a params array spread. A lone
    // params array passed as null is null: the frameworks take it as one null value.
    private static List<TypedConstant>? Values(AttributeData attribute)
    {
        var parameters = attribute.AttributeConstructor?.Parameters
            ?? System.Collections.Immutable.ImmutableArray<IParameterSymbol>.Empty;
        var arguments = attribute.ConstructorArguments;
        var values = new List<TypedConstant>();
        for (int i = 0; i < arguments.Length; i++)
        {
            bool spread = i == parameters.Length - 1 && parameters[i].IsParams && arguments[i].Kind == TypedConstantKind.Array;
            if (spread && arguments[i].IsNull && arguments.Length == 1)
            {
                return null;
            }
            if (spread && !arguments[i].IsNull)
            {
                values.AddRange(arguments[i].Values);
            }
            else
            {
                values.Add(arguments[i]);
            }
        }
        return values;
    }

    private static string? EnumName(TypedConstant constant)
    {
        if (constant.Type is not INamedTypeSymbol type)
        {
            return null;
        }
        var matches = type.GetMembers().OfType<IFieldSymbol>()
            .Where(f => f.HasConstantValue && Equals(f.ConstantValue, constant.Value))
            .Select(f => f.Name)
            .ToList();
        return matches.Count == 1 ? matches[0] : null;
    }

    // ---------------------------------------------------------------- NUnit 3

    public static List<string> NUnit(IMethodSymbol method, IEnumerable<AttributeData> attributes, ref bool exact)
    {
        var all = attributes.ToList();
        var cases = all.Where(a => Named(a, "NUnit.Framework", "TestCaseAttribute")).ToList();
        var names = new List<string>();
        if (all.Any(a => Named(a, "NUnit.Framework", "TestCaseSourceAttribute") || Named(a, "NUnit.Framework", "TheoryAttribute")))
        {
            exact = false;
        }
        if (cases.Count == 0)
        {
            if (method.Parameters.Length > 0)
            {
                exact = false; // [Values], [Range] and the like
            }
            else
            {
                names.Add(method.Name);
            }
            return names;
        }
        foreach (var attribute in cases)
        {
            string? given = NamedString(attribute, "TestName");
            if (given != null)
            {
                exact &= given.IndexOf('{') < 0;
                names.Add(given);
                continue;
            }
            var values = Values(attribute);
            var shown = new List<string>();
            foreach (var value in values ?? new List<TypedConstant>())
            {
                string? text = NUnitValue(value);
                exact &= text != null;
                shown.Add(text ?? "?");
            }
            if (values == null)
            {
                shown.Add("null");
            }
            names.Add(method.Name + "(" + string.Join(",", shown) + ")");
        }
        return names;
    }

    private static string? NUnitValue(TypedConstant constant)
    {
        if (constant.IsNull)
        {
            return "null";
        }
        switch (constant.Kind)
        {
            case TypedConstantKind.Enum:
                return EnumName(constant);
            case TypedConstantKind.Primitive:
                break;
            default:
                return null; // typeof and arrays
        }
        switch (constant.Value)
        {
            case string s:
                return "\"" + string.Concat(s.Select(c => c == '\'' ? "'" : NUnitEscape(c))) + "\"";
            case char c:
                return "'" + (c == '"' ? "\"" : NUnitEscape(c)) + "'";
            case bool b:
                return b ? "True" : "False";
            case double d:
                if (double.IsNaN(d)) return "double.NaN";
                if (double.IsPositiveInfinity(d)) return "double.PositiveInfinity";
                if (double.IsNegativeInfinity(d)) return "double.NegativeInfinity";
                if (d == double.MaxValue) return "double.MaxValue";
                if (d == double.MinValue) return "double.MinValue";
                return Decimals(Convert.ToString(d, System.Globalization.CultureInfo.InvariantCulture)) + "d";
            case float f:
                if (float.IsNaN(f)) return "float.NaN";
                if (float.IsPositiveInfinity(f)) return "float.PositiveInfinity";
                if (float.IsNegativeInfinity(f)) return "float.NegativeInfinity";
                if (f == float.MaxValue) return "float.MaxValue";
                if (f == float.MinValue) return "float.MinValue";
                return Decimals(Convert.ToString(f, System.Globalization.CultureInfo.InvariantCulture)) + "f";
            case long l:
                return l == long.MaxValue ? "long.MaxValue" : l == long.MinValue ? "long.MinValue" : l + "L";
            case ulong u:
                return u == ulong.MaxValue ? "ulong.MaxValue" : u == ulong.MinValue ? "ulong.MinValue" : u + "UL";
            case int i:
                return i == int.MaxValue ? "int.MaxValue" : i == int.MinValue ? "int.MinValue" : i.ToString(System.Globalization.CultureInfo.InvariantCulture);
            case uint u:
                return u.ToString(System.Globalization.CultureInfo.InvariantCulture);
            case short s:
                return s == short.MaxValue ? "short.MaxValue" : s == short.MinValue ? "short.MinValue" : s.ToString(System.Globalization.CultureInfo.InvariantCulture);
            case ushort u:
                return u == ushort.MaxValue ? "ushort.MaxValue" : u == ushort.MinValue ? "ushort.MinValue" : u.ToString(System.Globalization.CultureInfo.InvariantCulture);
            case byte b:
                return b == byte.MaxValue ? "byte.MaxValue" : b == byte.MinValue ? "byte.MinValue" : b.ToString(System.Globalization.CultureInfo.InvariantCulture);
            case sbyte s:
                return s == sbyte.MaxValue ? "sbyte.MaxValue" : s == sbyte.MinValue ? "sbyte.MinValue" : s.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }
        return null;
    }

    private static string Decimals(string text) => text.IndexOf('.') < 0 ? text + ".0" : text;

    private static string NUnitEscape(char c) => c switch
    {
        '\'' => "\\'",
        '"' => "\\\"",
        '\\' => "\\\\",
        '\0' => "\\0",
        '\a' => "\\a",
        '\b' => "\\b",
        '\f' => "\\f",
        '\n' => "\\n",
        '\r' => "\\r",
        '\t' => "\\t",
        '\v' => "\\v",
        '\x0085' or '\x2028' or '\x2029' => string.Format("\\x{0:X4}", (int)c),
        _ => c.ToString(),
    };

    // ---------------------------------------------------------------- xUnit 2

    public static List<string> Xunit(IMethodSymbol method, IEnumerable<AttributeData> attributes, ref bool exact)
    {
        var all = attributes.ToList();
        var fact = all.FirstOrDefault(a => a.AttributeClass is { } c && (c.Name == "FactAttribute" || c.Name == "TheoryAttribute"));
        string display = (fact != null ? NamedString(fact, "DisplayName") : null) ?? method.Name;
        var rows = all.Where(a => Named(a, "Xunit", "InlineDataAttribute")).ToList();
        var names = new List<string>();
        // Any other data attribute (MemberData, ClassData, a project's own) has cases the source
        // can't tell.
        if (all.Any(a => !Named(a, "Xunit", "InlineDataAttribute") && IsData(a.AttributeClass)))
        {
            exact = false;
        }
        if (rows.Count == 0)
        {
            if (method.Parameters.Length > 0)
            {
                exact = false;
            }
            else
            {
                names.Add(display);
            }
            return names;
        }
        foreach (var row in rows)
        {
            var values = Values(row);
            int count = values?.Count ?? 1;
            if (count != method.Parameters.Length || method.Parameters.Any(p => p.IsParams))
            {
                exact = false; // xUnit fills in optional parameters, and gathers params arrays
            }
            var shown = new List<string>();
            for (int i = 0; i < count; i++)
            {
                string parameter = i < method.Parameters.Length ? method.Parameters[i].Name : "???";
                string? text = values == null ? "null" : XunitValue(values[i]);
                exact &= text != null;
                shown.Add(parameter + ": " + (text ?? "?"));
            }
            names.Add(display + "(" + string.Join(", ", shown) + ")");
        }
        return names;
    }

    private static bool IsData(INamedTypeSymbol? type)
    {
        for (var t = type; t != null; t = t.BaseType)
        {
            if (t.Name == "DataAttribute" && t.ContainingNamespace?.ToDisplayString() == "Xunit.Sdk")
            {
                return true;
            }
        }
        return false;
    }

    private static string? XunitValue(TypedConstant constant)
    {
        if (constant.IsNull)
        {
            return "null";
        }
        if (constant.Kind == TypedConstantKind.Enum)
        {
            return EnumName(constant);
        }
        if (constant.Kind != TypedConstantKind.Primitive)
        {
            return null;
        }
        var invariant = System.Globalization.CultureInfo.InvariantCulture;
        switch (constant.Value)
        {
            case string s:
                // Escapes and long strings are shown the framework's own way.
                if (s.Length > 50 || s.Any(c => char.IsControl(c) || c == '\\'))
                {
                    return null;
                }
                return "\"" + s.Replace("\"", "\\\"") + "\"";
            case char c:
                if (c == '\'')
                {
                    return "'\\''";
                }
                return char.IsLetterOrDigit(c) || char.IsPunctuation(c) || char.IsSymbol(c) || c == ' ' ? "'" + c + "'" : null;
            case bool b:
                return b ? "True" : "False";
            case double d:
                return d.ToString("G17", invariant);
            case float f:
                return f.ToString("G9", invariant);
            default:
                return Convert.ToString(constant.Value, invariant);
        }
    }

    // ---------------------------------------------------------------- MSTest 2 and 3

    // The newest MSTest whose names are checked against its reports: a newer one may show a
    // string or a null its own way.
    private static readonly Version CheckedMSTest = new Version(3, 1, 1);

    public static List<string> MSTest(IMethodSymbol method, IEnumerable<AttributeData> attributes, Version? version, ref bool exact)
    {
        var all = attributes.ToList();
        const string space = "Microsoft.VisualStudio.TestTools.UnitTesting";
        var rows = all.Where(a => Named(a, space, "DataRowAttribute")).ToList();
        var names = new List<string>();
        if (all.Any(a => Named(a, space, "DynamicDataAttribute") || Named(a, space, "DataSourceAttribute")))
        {
            exact = false;
        }
        var test = all.FirstOrDefault(a => a.AttributeClass is { } c && (c.Name == "TestMethodAttribute" || c.Name == "DataTestMethodAttribute"));
        string? display = test != null && test.ConstructorArguments.Length == 1 ? test.ConstructorArguments[0].Value as string : null;
        if (rows.Count == 0)
        {
            if (method.Parameters.Length > 0)
            {
                exact = false;
            }
            else
            {
                names.Add(display ?? method.Name);
            }
            return names;
        }
        bool checkedVersion = version != null && version <= CheckedMSTest;
        foreach (var row in rows)
        {
            string? given = NamedString(row, "DisplayName");
            if (given != null)
            {
                names.Add(given);
                continue;
            }
            var values = Values(row);
            var shown = new List<string>();
            foreach (var value in values ?? new List<TypedConstant>())
            {
                string? text = MSTestValue(value);
                exact &= text != null && (checkedVersion || !(value.IsNull || value.Value is string));
                shown.Add(text ?? "?");
            }
            if (values == null)
            {
                exact = false;
            }
            names.Add(method.Name + " (" + string.Join(",", shown) + ")");
        }
        return names;
    }

    // MSTest joins its values' ToString(), null as nothing (2.2.10 and 3.1.1).
    private static string? MSTestValue(TypedConstant constant)
    {
        if (constant.IsNull)
        {
            return "";
        }
        if (constant.Kind == TypedConstantKind.Enum)
        {
            return EnumName(constant);
        }
        if (constant.Kind != TypedConstantKind.Primitive)
        {
            return null;
        }
        return constant.Value switch
        {
            bool b => b ? "True" : "False",
            double d => d.ToString(System.Globalization.CultureInfo.InvariantCulture),
            float f => f.ToString(System.Globalization.CultureInfo.InvariantCulture),
            _ => Convert.ToString(constant.Value, System.Globalization.CultureInfo.InvariantCulture),
        };
    }

}
