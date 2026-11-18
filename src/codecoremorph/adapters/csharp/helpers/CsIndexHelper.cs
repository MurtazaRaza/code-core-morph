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
// refuse (what a port's first version refuses), throw (an exception a member throws), test
// (a test method and the TRX names of its cases), dyn (a dynamic site and what the runtime binder
// binds there for each type that reaches it) and dynval (a dynamic value and its types, D-88).

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
using Microsoft.CodeAnalysis.Operations;

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
            if (port)
            {
                // The whole solution's flows: the tests' calls decide what reaches the library.
                DynamicSites.Finish();
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
        DynamicSites.Collect(compilation, tree);
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

    internal static string? TypeOf(SemanticModel model, SyntaxNode node) => EnclosingType(model, node);

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

    internal static void Refuse(SemanticModel model, string file, SyntaxNode node, string what, string detail)
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

// A port's dynamic sites (D-88): which of the solution's types reach each, and what C#'s runtime
// binder binds there for each, so a port makes each site a closed sum.
//
// The values that are dynamic live in nodes: a local, parameter, field, property or method
// return of type dynamic, and the elements of a List<dynamic> or dynamic[]. A node's types are
// the union of what flows into it: a value of a static type brings that type when nothing can
// derive from it (a struct, a sealed class, a built-in type) and otherwise the solution's
// concrete classes assignable to it; a site brings what it binds to. Each site is bound once per
// combination of its dynamic operands' types by speculative binding, each operand replaced by
// default(T): what the compiler binds for a T is what the binder binds for a value of runtime
// type T. Iterated to a fixed point. What it can't settle is a dynamic refusal at its site.
internal static class DynamicSites
{
    private const int MaxTypes = 16;
    private const int MaxCombinations = 64;
    private const int MaxRounds = 200;

    private static readonly SymbolDisplayFormat TypeName = new SymbolDisplayFormat(
        globalNamespaceStyle: SymbolDisplayGlobalNamespaceStyle.Omitted,
        typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameAndContainingTypesAndNamespaces,
        genericsOptions: SymbolDisplayGenericsOptions.IncludeTypeParameters,
        miscellaneousOptions: SymbolDisplayMiscellaneousOptions.UseSpecialTypes);

    private static readonly SymbolDisplayFormat MemberName = new SymbolDisplayFormat(
        globalNamespaceStyle: SymbolDisplayGlobalNamespaceStyle.Omitted,
        typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameAndContainingTypesAndNamespaces,
        genericsOptions: SymbolDisplayGenericsOptions.IncludeTypeParameters,
        memberOptions: SymbolDisplayMemberOptions.IncludeContainingType | SymbolDisplayMemberOptions.IncludeParameters,
        parameterOptions: SymbolDisplayParameterOptions.IncludeType | SymbolDisplayParameterOptions.IncludeParamsRefOut,
        miscellaneousOptions: SymbolDisplayMiscellaneousOptions.UseSpecialTypes);

    // The members of a List<dynamic> or dynamic[] the analysis follows; any other use of one
    // makes its elements unknown.
    private static readonly HashSet<string> ListMembers = new HashSet<string>(StringComparer.Ordinal)
    {
        "Add", "Insert", "Clear", "Remove", "RemoveAt", "Contains", "IndexOf", "Count", "Length",
        "this[]",
    };

    private sealed class TypeSet
    {
        public readonly SortedSet<string> Types = new SortedSet<string>(StringComparer.Ordinal);
        public string? Unknown;

        public bool Add(string type)
        {
            if (Unknown != null || !Types.Add(type))
            {
                return false;
            }
            if (Types.Count > MaxTypes)
            {
                Unknown = "more than " + MaxTypes + " types reach it";
            }
            return true;
        }

        public bool AddAll(TypeSet other)
        {
            if (Unknown != null)
            {
                return false;
            }
            if (other.Unknown != null)
            {
                Unknown = other.Unknown;
                return true;
            }
            bool changed = false;
            foreach (string t in other.Types)
            {
                changed |= Add(t);
            }
            return changed;
        }

        public static TypeSet Of(string? unknown) => new TypeSet { Unknown = unknown };
    }

    // Where a dynamic value comes from.
    private abstract class Source { }
    private sealed class StaticSource : Source { public ITypeSymbol Type = null!; }
    private sealed class NodeSource : Source { public string Node = ""; }
    private sealed class SiteSource : Source { public Site Site = null!; }
    private sealed class UnionSource : Source { public List<Source> Parts = new List<Source>(); }
    private sealed class UnknownSource : Source { public string Reason = ""; }

    private sealed class Binding
    {
        public List<string> Types = new List<string>();
        public string? Member;
        public string? MemberId;
        public TypeSet? Result;
        public string? ResultNode;
        public string? Error;
        public string? Unseen;
        public List<(string Node, string Type)> Into = new List<(string, string)>();
    }

    private sealed class Site
    {
        public IOperation Op = null!;
        public SemanticModel Model = null!;
        public string File = "";
        public SyntaxNode Node = null!;
        public string Kind = "";
        public List<IOperation> Operands = new List<IOperation>();
        public List<Source> Sources = new List<Source>();
        public ITypeSymbol? ConvertTo;
        public List<Binding> Bindings = new List<Binding>();
        public string Signature = "";
        public TypeSet Result = new TypeSet();
        public string? Unknown;
    }

    private sealed class Node
    {
        public TypeSet Types = new TypeSet();
        public ISymbol? Symbol;
        public string What = "";
        public string? File;
        public SyntaxNode? Declaration;
        public SemanticModel? Model;
        public bool Public;
        public bool Called;
        public bool Elements;
    }

    private static readonly Dictionary<string, Node> Nodes = new Dictionary<string, Node>(StringComparer.Ordinal);
    private static readonly List<(string Node, Source From)> Flows = new List<(string, Source)>();
    private static readonly List<Site> Sites = new List<Site>();
    private static readonly Dictionary<IOperation, Site> SiteOf = new Dictionary<IOperation, Site>();
    private static readonly Dictionary<string, ITypeSymbol> TypeOf = new Dictionary<string, ITypeSymbol>(StringComparer.Ordinal);
    private static readonly List<INamedTypeSymbol> SourceTypes = new List<INamedTypeSymbol>();
    private static readonly List<(SemanticModel Model, string File, SyntaxNode Node, string Detail)> Uncovered = new List<(SemanticModel, string, SyntaxNode, string)>();
    private static readonly List<(SemanticModel Model, string File, IOperation Op)> Dynamic = new List<(SemanticModel, string, IOperation)>();
    // Each collected tree's model: a symbol another project's code reaches is declared in its
    // own project's tree.
    private static readonly Dictionary<SyntaxTree, SemanticModel> Models = new Dictionary<SyntaxTree, SemanticModel>();

    /// <summary>Collects a tree's dynamic values, flows and sites (port=1).</summary>
    public static void Collect(CSharpCompilation compilation, SyntaxTree tree)
    {
        // The binder checks accessibility from the call site: so does the speculative binding.
        var model = compilation.GetSemanticModel(tree);
        Models[tree] = model;
        string file = tree.FilePath;
        foreach (var declaration in tree.GetRoot().DescendantNodes().OfType<BaseTypeDeclarationSyntax>())
        {
            if (model.GetDeclaredSymbol(declaration) is INamedTypeSymbol type)
            {
                SourceTypes.Add(type);
            }
        }
        var roots = tree.GetRoot().DescendantNodes().Where(n =>
            n is BaseMethodDeclarationSyntax || n is AccessorDeclarationSyntax
            || (n is ArrowExpressionClauseSyntax && n.Parent is BasePropertyDeclarationSyntax)
            || (n is EqualsValueClauseSyntax && (n.Parent is VariableDeclaratorSyntax { Parent: VariableDeclarationSyntax { Parent: FieldDeclarationSyntax } } || n.Parent is PropertyDeclarationSyntax)));
        foreach (var node in roots)
        {
            IOperation? body;
            try
            {
                body = model.GetOperation(node);
            }
            catch (Exception)
            {
                continue;
            }
            if (body == null)
            {
                continue;
            }
            foreach (var op in body.DescendantsAndSelf())
            {
                Visit(model, file, op);
            }
        }
    }

    private static void Visit(SemanticModel model, string file, IOperation op)
    {
        if (Dyn(op.Type))
        {
            Dynamic.Add((model, file, op));
        }
        switch (op)
        {
            case IFieldInitializerOperation init:
                foreach (var f in init.InitializedFields.Where(f => Dyn(f.Type)))
                {
                    Flow(Declare(f, model), init.Value);
                }
                break;
            case IPropertyInitializerOperation init:
                foreach (var p in init.InitializedProperties.Where(p => Dyn(p.Type)))
                {
                    Flow(Declare(p, model), init.Value);
                }
                break;
            case IVariableDeclaratorOperation v when v.Initializer != null && Dyn(v.Symbol.Type):
                Flow(Declare(v.Symbol, model), v.Initializer.Value);
                break;
            case ISimpleAssignmentOperation a when Dyn(a.Target.Type) && Target(a.Target, model) is string target:
                Flow(target, a.Value);
                break;
            case ICompoundAssignmentOperation c when (Dyn(c.Target.Type) && Target(c.Target, model) is string compound):
                Flow(compound, new SiteSource { Site = SiteFor(model, file, op) });
                break;
            case ICompoundAssignmentOperation c when Dyn(c.Value.Type):
                SiteFor(model, file, op);
                break;
            case IArgumentOperation arg when arg.Parameter != null && Dyn(arg.Parameter.Type) && InSource(arg.Parameter):
                Flow(Declare(arg.Parameter.OriginalDefinition, model), arg.Value);
                Nodes[NodeKey(arg.Parameter.OriginalDefinition)].Called = true;
                break;
            case IReturnOperation ret when ret.ReturnedValue != null && EnclosingMethod(model, op) is IMethodSymbol m && Dyn(m.ReturnType):
                Flow(m.AssociatedSymbol is IPropertySymbol owner ? Declare(owner.OriginalDefinition, model) : Declare(m.OriginalDefinition, model, returns: true), ret.ReturnedValue);
                break;
            case IInvocationOperation call when call.Instance != null && Elements(call.Instance, model) is string elements
                && (call.TargetMethod.Name == "Add" || call.TargetMethod.Name == "Insert"):
                Flow(elements, call.Arguments.Last().Value);
                break;
            case IForEachLoopOperation loop when Elements(loop.Collection, model) is string looped:
                foreach (var local in loop.Locals)
                {
                    Flow(Declare(local, model), new NodeSource { Node = looped });
                }
                break;
            case IMethodReferenceOperation reference when reference.Method.Parameters.Any(p => Dyn(p.Type)) && InSource(reference.Method)
                && reference.Parent is not IDynamicInvocationOperation:
                // Used as a delegate: its callers aren't the solution's calls.
                foreach (var p in reference.Method.OriginalDefinition.Parameters.Where(p => Dyn(p.Type)))
                {
                    Flow(Declare(p, model), new UnknownSource { Reason = reference.Method.ToDisplayString(MemberName) + " is used as a delegate, whose callers the port can't follow" });
                }
                break;
            case IConversionOperation conv when Dyn(conv.Operand.Type) && !Dyn(conv.Type) && conv.Type?.SpecialType != SpecialType.System_Object:
                SiteFor(model, file, op);
                break;
            case IDynamicInvocationOperation:
            case IDynamicIndexerAccessOperation:
            case IDynamicObjectCreationOperation:
                SiteFor(model, file, op);
                break;
            case IDynamicMemberReferenceOperation member when !(member.Parent is IDynamicInvocationOperation inv && inv.Operation == member):
                SiteFor(model, file, op);
                break;
            case IBinaryOperation bin when Dyn(bin.Type) || Dyn(bin.LeftOperand.Type) || Dyn(bin.RightOperand.Type):
                SiteFor(model, file, op);
                break;
            case IUnaryOperation un when Dyn(un.Operand.Type):
                SiteFor(model, file, op);
                break;
            case IInterpolationOperation interpolation when Dyn(interpolation.Expression.Type):
                SiteFor(model, file, op);
                break;
            case IIncrementOrDecrementOperation step when Dyn(step.Target.Type):
                if (Target(step.Target, model) is string stepped)
                {
                    Flow(stepped, new SiteSource { Site = SiteFor(model, file, op) });
                }
                else
                {
                    SiteFor(model, file, op);
                }
                break;
        }
        // A List<dynamic> the analysis can't follow: its elements are unknown.
        if (CollectionSymbol(op) is ISymbol collection && !Followed(op))
        {
            string key = "elements:" + NodeKey(collection.OriginalDefinition);
            Flow(DeclareElements(key, collection, model), new UnknownSource { Reason = "the list " + collection.Name + " is used where the port can't follow its elements (" + (op.Parent?.Kind.ToString() ?? "") + ")" });
        }
    }

    // The symbol of a reference to a List<dynamic> or dynamic[], or null.
    private static ISymbol? CollectionSymbol(IOperation op)
    {
        ISymbol? symbol = op switch
        {
            IFieldReferenceOperation f => f.Field,
            ILocalReferenceOperation l => l.Local,
            IPropertyReferenceOperation { Arguments.Length: 0 } p => p.Property,
            IParameterReferenceOperation p => p.Parameter,
            _ => null,
        };
        return symbol != null && DynamicElements(op.Type) ? symbol : null;
    }

    // Whether a reference to a List<dynamic> is a use the analysis follows.
    private static bool Followed(IOperation reference)
    {
        var parent = reference.Parent;
        while (parent is IConversionOperation { IsImplicit: true } c && c.Operand == reference)
        {
            reference = parent;
            parent = parent.Parent;
        }
        switch (parent)
        {
            case IInvocationOperation call when call.Instance == reference:
                return ListMembers.Contains(call.TargetMethod.Name);
            case IPropertyReferenceOperation property when property.Instance == reference:
                return ListMembers.Contains(property.Property.IsIndexer ? "this[]" : property.Property.Name);
            case IArrayElementReferenceOperation element when element.ArrayReference == reference:
                return true;
            case IForEachLoopOperation loop when loop.Collection == reference:
                return true;
            case ISimpleAssignmentOperation assignment when assignment.Target == reference:
                return assignment.Value is IObjectCreationOperation { Initializer: null } or IArrayCreationOperation { Initializer: null };
            case IPropertyReferenceOperation:
                return false;
        }
        return false;
    }

    private static bool DynamicElements(ITypeSymbol? type) =>
        type is IArrayTypeSymbol a && Dyn(a.ElementType)
        || type is INamedTypeSymbol { IsGenericType: true } g && g.TypeArguments.Length == 1 && Dyn(g.TypeArguments[0])
            && g.ContainingNamespace?.ToDisplayString() == "System.Collections.Generic";

    // The elements' node of a List<dynamic> or dynamic[] the expression reads or writes.
    private static string? Elements(IOperation collection, SemanticModel model)
    {
        while (collection is IConversionOperation { IsImplicit: true } c)
        {
            collection = c.Operand;
        }
        if (collection is IPropertyReferenceOperation { Property.IsIndexer: true, Instance: { } indexed })
        {
            return Elements(indexed, model) is string inner ? inner : null;
        }
        if (collection is IArrayElementReferenceOperation element)
        {
            return Elements(element.ArrayReference, model);
        }
        var symbol = CollectionSymbol(collection);
        if (symbol == null)
        {
            return null;
        }
        string key = "elements:" + NodeKey(symbol.OriginalDefinition);
        return DeclareElements(key, symbol, model);
    }

    private static string? Target(IOperation target, SemanticModel model) => target switch
    {
        ILocalReferenceOperation l => Declare(l.Local, model),
        IFieldReferenceOperation f => Declare(f.Field.OriginalDefinition, model),
        IPropertyReferenceOperation { Arguments.Length: 0 } p => Declare(p.Property.OriginalDefinition, model),
        IParameterReferenceOperation p => Declare(p.Parameter.OriginalDefinition, model),
        IPropertyReferenceOperation { Property.IsIndexer: true } or IArrayElementReferenceOperation => Elements(target, model),
        _ => null,
    };

    private static string Declare(ISymbol symbol, SemanticModel model, bool returns = false)
    {
        string key = returns ? "return:" + NodeKey(symbol) : NodeKey(symbol);
        if (Nodes.ContainsKey(key))
        {
            return key;
        }
        var node = new Node { Symbol = symbol };
        Place(node, symbol, model);
        switch (symbol)
        {
            case IParameterSymbol p:
                var method = p.ContainingSymbol as IMethodSymbol;
                node.What = "parameter " + p.Name + " of " + p.ContainingSymbol.ToDisplayString(MemberName);
                node.Public = method != null && Exposed(method);
                if (method != null && (method.IsVirtual || method.IsAbstract || method.IsOverride || Implements(method)))
                {
                    Flows.Add((key, new UnknownSource { Reason = "a dynamic parameter of the virtual member " + method.ToDisplayString(MemberName) + ", whose callers through its base the port can't follow" }));
                }
                if (method is { MethodKind: MethodKind.AnonymousFunction })
                {
                    Flows.Add((key, new UnknownSource { Reason = "a lambda's dynamic parameter, whose callers the port can't follow" }));
                }
                if (p.RefKind != RefKind.None)
                {
                    Flows.Add((key, new UnknownSource { Reason = "a dynamic " + p.RefKind.ToString().ToLowerInvariant() + " parameter" }));
                }
                break;
            case IMethodSymbol m:
                node.What = "the return of " + m.ToDisplayString(MemberName);
                node.Public = Exposed(m);
                break;
            case IPropertySymbol prop:
                node.What = "property " + prop.ToDisplayString(MemberName);
                node.Public = Exposed(prop);
                if (prop.SetMethod != null && Exposed(prop.SetMethod))
                {
                    Flows.Add((key, new UnknownSource { Reason = "the public setter of " + prop.ToDisplayString(MemberName) + ", which callers outside the solution can set" }));
                }
                break;
            case IFieldSymbol field:
                node.What = "field " + field.ToDisplayString(MemberName);
                node.Public = Exposed(field);
                if (node.Public && !field.IsReadOnly)
                {
                    Flows.Add((key, new UnknownSource { Reason = "the public field " + field.ToDisplayString(MemberName) + ", which callers outside the solution can set" }));
                }
                break;
            case ILocalSymbol local:
                node.What = "local " + local.Name + " of " + (local.ContainingSymbol?.ToDisplayString(MemberName) ?? "");
                break;
        }
        Nodes[key] = node;
        return key;
    }

    private static string DeclareElements(string key, ISymbol collection, SemanticModel model)
    {
        if (!Nodes.ContainsKey(key))
        {
            collection = collection.OriginalDefinition;
            var node = new Node { Symbol = collection, Elements = true };
            Place(node, collection, model);
            string what = collection switch
            {
                IFieldSymbol f => "field " + f.ToDisplayString(MemberName),
                IPropertySymbol p => "property " + p.ToDisplayString(MemberName),
                ILocalSymbol l => "local " + l.Name + " of " + (l.ContainingSymbol?.ToDisplayString(MemberName) ?? ""),
                IParameterSymbol p => "parameter " + p.Name + " of " + p.ContainingSymbol.ToDisplayString(MemberName),
                _ => collection.Name,
            };
            node.What = "the elements of " + what;
            node.Public = collection is not ILocalSymbol && Exposed(collection);
            Nodes[key] = node;
            if (node.Public || collection is IParameterSymbol)
            {
                Flows.Add((key, new UnknownSource { Reason = "the list " + collection.ToDisplayString(MemberName) + ", which callers outside the type can fill" }));
            }
        }
        return key;
    }

    // Where a node's symbol is declared.
    private static void Place(Node node, ISymbol symbol, SemanticModel model)
    {
        var location = symbol.Locations.FirstOrDefault(l => l.IsInSource);
        var tree = location?.SourceTree;
        if (tree == null)
        {
            return;
        }
        if (!Models.TryGetValue(tree, out var own))
        {
            // Not a tree of the projects walked so far: only its own compilation has a model.
            own = model.Compilation.ContainsSyntaxTree(tree) ? model.Compilation.GetSemanticModel(tree) : null;
        }
        if (own == null)
        {
            return;
        }
        node.File = tree.FilePath;
        node.Declaration = tree.GetRoot().FindNode(location!.SourceSpan);
        node.Model = own;
    }

    private static void Flow(string node, IOperation value)
    {
        Flows.Add((node, SourceOf(value)));
    }

    private static void Flow(string node, Source from)
    {
        Flows.Add((node, from));
    }

    // Where a value that is, or becomes, dynamic comes from.
    private static Source SourceOf(IOperation value)
    {
        while (value is IConversionOperation conv && Dyn(conv.Type))
        {
            value = conv.Operand; // to dynamic: the operand's type
        }
        if (value is IParenthesizedOperation parenthesized)
        {
            return SourceOf(parenthesized.Operand);
        }
        if (SiteOf.TryGetValue(value, out var known))
        {
            return new SiteSource { Site = known };
        }
        if (value is ILiteralOperation { ConstantValue: { HasValue: true, Value: null } })
        {
            return new UnknownSource { Reason = "a null the port can't type" };
        }
        if (!Dyn(value.Type))
        {
            return value.Type == null || value.Type.TypeKind == TypeKind.Error
                ? new UnknownSource { Reason = "a value without a type" }
                : new StaticSource { Type = value.Type };
        }
        var model = value.SemanticModel!;
        ISymbol? outside = value switch
        {
            IFieldReferenceOperation f => f.Field,
            IPropertyReferenceOperation { Arguments.Length: 0 } p => p.Property,
            IInvocationOperation call => call.TargetMethod,
            _ => null,
        };
        if (outside != null && !InSource(outside))
        {
            return new UnknownSource { Reason = "the dynamic value of " + outside.ToDisplayString(MemberName) + ", from outside the solution" };
        }
        switch (value)
        {
            case ILocalReferenceOperation l: return new NodeSource { Node = Declare(l.Local, model) };
            case IParameterReferenceOperation p: return new NodeSource { Node = Declare(p.Parameter.OriginalDefinition, model) };
            case IFieldReferenceOperation f: return new NodeSource { Node = Declare(f.Field.OriginalDefinition, model) };
            case IPropertyReferenceOperation { Arguments.Length: 0 } p: return new NodeSource { Node = Declare(p.Property.OriginalDefinition, model) };
            case IPropertyReferenceOperation { Property.IsIndexer: true } when Elements(value, model) is string indexed:
                return new NodeSource { Node = indexed };
            case IArrayElementReferenceOperation when Elements(value, model) is string element:
                return new NodeSource { Node = element };
            case IInvocationOperation call: return new NodeSource { Node = Declare(call.TargetMethod.OriginalDefinition, model, returns: true) };
            case IConditionalOperation choice when choice.WhenFalse != null:
                return new UnionSource { Parts = { SourceOf(choice.WhenTrue), SourceOf(choice.WhenFalse) } };
            case ICoalesceOperation coalesce:
                return new UnionSource { Parts = { SourceOf(coalesce.Value), SourceOf(coalesce.WhenNull) } };
            case IDynamicInvocationOperation:
            case IDynamicMemberReferenceOperation:
            case IDynamicIndexerAccessOperation:
            case IBinaryOperation:
            case IUnaryOperation:
            case ICompoundAssignmentOperation:
            case IIncrementOrDecrementOperation:
                return new SiteSource { Site = SiteFor(model, value.Syntax.SyntaxTree.FilePath, value) };
        }
        return new UnknownSource { Reason = "a dynamic " + value.Kind };
    }

    private static Site SiteFor(SemanticModel model, string file, IOperation op)
    {
        if (SiteOf.TryGetValue(op, out var known))
        {
            return known;
        }
        var site = new Site { Op = op, Model = model, File = file, Node = op.Syntax };
        SiteOf[op] = site;
        Sites.Add(site);
        switch (op)
        {
            case IDynamicInvocationOperation inv:
                site.Kind = "invoke";
                if (inv.Operation is IDynamicMemberReferenceOperation callee)
                {
                    if (callee.Instance != null && Dyn(callee.Instance.Type))
                    {
                        site.Operands.Add(callee.Instance);
                    }
                }
                else if (Dyn(inv.Operation.Type))
                {
                    site.Operands.Add(inv.Operation); // a dynamic delegate
                }
                site.Operands.AddRange(inv.Arguments.Where(a => Dyn(a.Type)));
                break;
            case IDynamicMemberReferenceOperation member:
                site.Kind = "member";
                if (member.Instance != null)
                {
                    site.Operands.Add(member.Instance);
                }
                break;
            case IDynamicIndexerAccessOperation index:
                site.Kind = "index";
                if (Dyn(index.Operation.Type))
                {
                    site.Operands.Add(index.Operation);
                }
                site.Operands.AddRange(index.Arguments.Where(a => Dyn(a.Type)));
                break;
            case IDynamicObjectCreationOperation create:
                site.Kind = "new";
                site.Operands.AddRange(create.Arguments.Where(a => Dyn(a.Type)));
                break;
            case IBinaryOperation bin:
                site.Kind = "binary";
                if (Dyn(bin.LeftOperand.Type)) site.Operands.Add(bin.LeftOperand);
                if (Dyn(bin.RightOperand.Type)) site.Operands.Add(bin.RightOperand);
                break;
            case IUnaryOperation un:
                site.Kind = "unary";
                site.Operands.Add(un.Operand);
                break;
            case ICompoundAssignmentOperation c:
                site.Kind = "compound";
                if (Dyn(c.Target.Type)) site.Operands.Add(c.Target);
                if (Dyn(c.Value.Type)) site.Operands.Add(c.Value);
                break;
            case IIncrementOrDecrementOperation step:
                site.Kind = "step";
                site.Operands.Add(step.Target);
                break;
            case IInterpolationOperation interpolation:
                site.Kind = "format";
                site.Operands.Add(interpolation.Expression);
                break;
            case IConversionOperation conv:
                site.Kind = conv.IsImplicit ? "convert" : "cast";
                site.Operands.Add(conv.Operand);
                site.ConvertTo = conv.Type;
                break;
        }
        foreach (var operand in site.Operands)
        {
            site.Sources.Add(SourceOf(operand));
        }
        return site;
    }

    /// <summary>Solves the flows and writes the dyn and dynval records and the refusals.</summary>
    public static void Finish()
    {
        if (Sites.Count == 0 && Nodes.Count == 0)
        {
            return;
        }
        Cover();
        Solve();
        // A public dynamic parameter no caller in the solution passes: callers outside it decide.
        bool uncalled = false;
        foreach (var node in Nodes.Values)
        {
            if (node.Symbol is IParameterSymbol && node.Public && node.Types.Unknown == null && node.Types.Types.Count == 0 && !node.Called)
            {
                node.Types.Unknown = "no caller in the solution passes it a value, so the types that reach it are unknown";
                uncalled = true;
            }
        }
        if (uncalled)
        {
            Solve();
        }
        foreach (var (model, file, node, detail) in Uncovered)
        {
            PortSource.Refuse(model, file, node, "dynamic", detail);
        }
        WriteValues();
        WriteSites();
    }

    private static void Solve()
    {
        for (int round = 0; ; round++)
        {
            bool changed = false;
            foreach (var (node, from) in Flows)
            {
                changed |= Nodes[node].Types.AddAll(Eval(from));
            }
            foreach (var site in Sites)
            {
                changed |= Bind(site);
            }
            if (!changed)
            {
                return;
            }
            if (round == MaxRounds)
            {
                foreach (var site in Sites)
                {
                    site.Unknown ??= "the analysis found no fixed point";
                }
                return;
            }
        }
    }

    // Each dynamic value is used where the analysis follows it; anything else is a refusal.
    private static void Cover()
    {
        foreach (var (model, file, op) in Dynamic)
        {
            if (Covered(op))
            {
                continue;
            }
            string where = op.Parent == null ? "" : " in a " + Words(op.Parent.Kind.ToString());
            Uncovered.Add((model, file, op.Syntax, op.Syntax + ": a dynamic value" + where + ", which the port doesn't cover"));
        }
    }

    private static bool Covered(IOperation op)
    {
        var parent = op.Parent;
        if (parent == null)
        {
            return true;
        }
        if (SiteOf.TryGetValue(parent, out var site) && site.Operands.Contains(op))
        {
            return true;
        }
        switch (parent)
        {
            case IDynamicMemberReferenceOperation callee when callee.Parent is IDynamicInvocationOperation call
                && call.Operation == callee && SiteOf.TryGetValue(call, out var invoked) && invoked.Operands.Contains(op):
                return true; // the receiver of a dynamic call
            case IDynamicInvocationOperation inv when inv.Operation == op && op is IDynamicMemberReferenceOperation:
            case IConversionOperation:
            case ISimpleAssignmentOperation:
            case ICompoundAssignmentOperation:
            case IVariableInitializerOperation:
            case IFieldInitializerOperation:
            case IPropertyInitializerOperation:
            case IArgumentOperation:
            case IReturnOperation:
            case IExpressionStatementOperation:
            case IParenthesizedOperation:
            case IIsTypeOperation:
            case IIsPatternOperation:
            case IIncrementOrDecrementOperation:
                return true;
            case IConditionalOperation choice:
                return choice.Condition != op && Covered(parent);
            case ICoalesceOperation:
                return Covered(parent);
        }
        return false;
    }

    private static TypeSet Eval(Source from)
    {
        switch (from)
        {
            case StaticSource s:
                return Concrete(s.Type);
            case NodeSource n:
                return Nodes.TryGetValue(n.Node, out var node) ? node.Types : new TypeSet();
            case SiteSource s:
                return s.Site.Result;
            case UnionSource u:
                var all = new TypeSet();
                foreach (var part in u.Parts)
                {
                    all.AddAll(Eval(part));
                }
                return all;
            case UnknownSource unknown:
                return TypeSet.Of(unknown.Reason);
        }
        throw new InvalidOperationException("an unknown source");
    }

    // The runtime types of a value of static type T.
    private static TypeSet Concrete(ITypeSymbol type)
    {
        var set = new TypeSet();
        if (type.IsAnonymousType)
        {
            return TypeSet.Of("a value of an anonymous type, which the port can't name");
        }
        if (type.TypeKind == TypeKind.TypeParameter)
        {
            return TypeSet.Of("a value of the type parameter " + type.Name + " (generic arithmetic through dynamic binds per type argument)");
        }
        if (LateBound(type) is string late)
        {
            return TypeSet.Of(late);
        }
        if (type.IsValueType || type.IsSealed || type.SpecialType == SpecialType.System_String || type.TypeKind == TypeKind.Array)
        {
            set.Add(Remember(type));
            return set;
        }
        string key = Key(type);
        if (!SourceTypes.Any(t => Key(t) == key))
        {
            return TypeSet.Of("a value of type " + type.ToDisplayString(TypeName) + ", which a type outside the solution can be");
        }
        foreach (var t in SourceTypes.Where(t => !t.IsAbstract && t.TypeKind != TypeKind.Interface && t.TypeKind != TypeKind.Delegate && Assignable(t, key)))
        {
            if (LateBound(t) is string derived)
            {
                return TypeSet.Of(derived);
            }
            set.Add(Remember(t));
        }
        return set;
    }

    // A type whose members bind at run time by its own rules, which the port can't see.
    private static string? LateBound(ITypeSymbol type)
    {
        if (type.AllInterfaces.Any(i => i.ToDisplayString() == "System.Dynamic.IDynamicMetaObjectProvider"))
        {
            return "late binding to " + type.ToDisplayString(TypeName) + " (an IDynamicMetaObjectProvider), which the port can't see";
        }
        if (type.GetAttributes().Any(a => a.AttributeClass?.Name == "ComImportAttribute"))
        {
            return "late binding to the COM object " + type.ToDisplayString(TypeName);
        }
        return null;
    }

    private static bool Assignable(INamedTypeSymbol t, string target)
    {
        for (ITypeSymbol? b = t; b != null; b = b.BaseType)
        {
            if (Key(b) == target)
            {
                return true;
            }
        }
        return t.AllInterfaces.Any(i => Key(i) == target);
    }

    private static string Remember(ITypeSymbol type)
    {
        string key = Key(type);
        if (!TypeOf.ContainsKey(key))
        {
            TypeOf[key] = type;
        }
        return key;
    }

    // Binds the site for each combination of its dynamic operands' types; true if anything grew.
    private static bool Bind(Site site)
    {
        var sets = site.Sources.Select(Eval).ToList();
        string? unknown = sets.FirstOrDefault(s => s.Unknown != null)?.Unknown;
        if (unknown != null)
        {
            bool fresh = site.Unknown == null;
            site.Unknown ??= unknown;
            site.Result.Unknown ??= site.Unknown;
            return fresh;
        }
        var combinations = new List<List<string>> { new List<string>() };
        foreach (var set in sets)
        {
            combinations = combinations.SelectMany(c => set.Types.Select(t => new List<string>(c) { t })).ToList();
            if (combinations.Count > MaxCombinations)
            {
                bool fresh = site.Unknown == null;
                site.Unknown ??= "more than " + MaxCombinations + " combinations of types reach it";
                site.Result.Unknown ??= site.Unknown;
                return fresh;
            }
        }
        string signature = string.Join(";", combinations.Select(c => string.Join(",", c)));
        bool changed = false;
        if (signature != site.Signature)
        {
            site.Signature = signature;
            site.Bindings = combinations.Where(c => c.Count == site.Operands.Count).Select(c => BindOne(site, c)).ToList();
            changed = true;
        }
        foreach (var b in site.Bindings)
        {
            if (b.Result != null)
            {
                changed |= site.Result.AddAll(b.Result);
            }
            if (b.ResultNode != null && Nodes.TryGetValue(b.ResultNode, out var returned))
            {
                changed |= site.Result.AddAll(returned.Types);
            }
            foreach (var (node, type) in b.Into)
            {
                if (Nodes.TryGetValue(node, out var into))
                {
                    changed |= into.Types.Add(type);
                    into.Called = true;
                }
            }
        }
        return changed;
    }

    private static Binding BindOne(Site site, List<string> types)
    {
        var binding = new Binding { Types = types };
        var unseen = types.Where(t => !Visible(TypeOf[t], site.Model.Compilation)).ToList();
        if (unseen.Count > 0)
        {
            binding.Unseen = string.Join(", ", unseen.Select(t => Display(TypeOf[t]) + Where(TypeOf[t])));
            return binding;
        }
        if (site.ConvertTo != null)
        {
            var from = TypeOf[types[0]];
            var conversion = site.Model.Compilation.ClassifyConversion(from, site.ConvertTo);
            bool ok = conversion.Exists && (site.Kind == "cast" || conversion.IsImplicit);
            if (!ok)
            {
                binding.Error = "Cannot " + (site.Kind == "cast" ? "convert" : "implicitly convert") + " type '"
                    + from.ToDisplayString() + "' to '" + site.ConvertTo.ToDisplayString() + "'";
                return binding;
            }
            binding.Member = conversion.IsUserDefined && conversion.MethodSymbol != null
                ? conversion.MethodSymbol.ToDisplayString(MemberName)
                : Describe(conversion) + " conversion to " + site.ConvertTo.ToDisplayString(TypeName);
            binding.Result = Concrete(site.ConvertTo);
            return binding;
        }
        // The site's expression with each dynamic operand replaced by default(T).
        var expression = (ExpressionSyntax)site.Op.Syntax;
        var replacements = new Dictionary<SyntaxNode, SyntaxNode>();
        for (int i = 0; i < site.Operands.Count; i++)
        {
            var typed = SyntaxFactory.ParseExpression("default(" + TypeOf[types[i]].ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat) + ")");
            replacements[site.Operands[i].Syntax] = SyntaxFactory.ParenthesizedExpression(typed);
        }
        if (site.Op is ICompoundAssignmentOperation)
        {
            // The binder computes target op value, then assigns it: bind the operation.
            var assignment = (AssignmentExpressionSyntax)expression;
            var left = replacements.TryGetValue(assignment.Left, out var l) ? (ExpressionSyntax)l : assignment.Left;
            var right = replacements.TryGetValue(assignment.Right, out var r) ? (ExpressionSyntax)r : assignment.Right;
            string op = assignment.OperatorToken.ValueText.Substring(0, assignment.OperatorToken.ValueText.Length - 1);
            expression = SyntaxFactory.ParseExpression("(" + left + ") " + op + " (" + right + ")");
        }
        else if (site.Op is IInterpolationOperation)
        {
            // An interpolation formats the value with its ToString.
            expression = SyntaxFactory.ParseExpression("(" + replacements.Values.First() + ").ToString()");
        }
        else if (site.Op is IIncrementOrDecrementOperation step)
        {
            expression = SyntaxFactory.ParseExpression("(" + replacements.Values.First() + ") " + (step.Kind == OperationKind.Increment ? "+" : "-") + " 1");
        }
        else
        {
            expression = expression.ReplaceNodes(replacements.Keys, (original, _) => replacements[original]);
        }
        var statement = SyntaxFactory.ExpressionStatement(expression);
        if (!site.Model.TryGetSpeculativeSemanticModel(site.Op.Syntax.SpanStart, statement, out var speculative) || speculative == null)
        {
            binding.Error = "the binder finds nothing for " + string.Join(", ", types.Select(t => Display(TypeOf[t])));
            return binding;
        }
        var bound = ((ExpressionStatementSyntax)statement).Expression;
        var info = speculative.GetSymbolInfo(bound);
        var typeInfo = speculative.GetTypeInfo(bound);
        var symbol = info.Symbol;
        if (symbol == null && speculative.GetOperation(bound) is IBinaryOperation { OperatorMethod: null } builtin
            && builtin.Type != null && builtin.Type.TypeKind != TypeKind.Error)
        {
            binding.Member = "built-in " + Words(builtin.OperatorKind.ToString()) + " of " + Display(builtin.LeftOperand.Type) + " and " + Display(builtin.RightOperand.Type);
        }
        else if (symbol == null)
        {
            binding.Error = BinderMessage(site, types, info);
            return binding;
        }
        else
        {
            binding.Member = symbol.ToDisplayString(MemberName);
            binding.MemberId = InSource(symbol) ? Indexer.Id(symbol.OriginalDefinition) : null;
        }
        var resultType = typeInfo.Type;
        if (symbol is IMethodSymbol method && method.MethodKind is not (MethodKind.BuiltinOperator or MethodKind.UserDefinedOperator))
        {
            resultType = method.ReturnType;
            var parameters = method.OriginalDefinition.Parameters;
            for (int i = 0; i < parameters.Length; i++)
            {
                // A dynamic call into the solution's method: its dynamic parameters get the
                // arguments' types.
                if (Dyn(parameters[i].Type) && InSource(method) && DynamicArgument(site, i) is int argument && argument >= 0)
                {
                    binding.Into.Add((Declare(parameters[i], site.Model), types[argument]));
                }
            }
            if (Dyn(method.ReturnType))
            {
                binding.ResultNode = method.AssociatedSymbol is IPropertySymbol owner
                    ? Declare(owner.OriginalDefinition, site.Model)
                    : Declare(method.OriginalDefinition, site.Model, returns: true);
                return binding;
            }
        }
        if (symbol is IPropertySymbol property && Dyn(property.Type))
        {
            binding.ResultNode = Declare(property.OriginalDefinition, site.Model);
            return binding;
        }
        if (symbol is IFieldSymbol field && Dyn(field.Type))
        {
            binding.ResultNode = Declare(field.OriginalDefinition, site.Model);
            return binding;
        }
        if (resultType != null && resultType.SpecialType != SpecialType.System_Void)
        {
            if (resultType.TypeKind == TypeKind.Error)
            {
                binding.Error = BinderMessage(site, types, info);
                binding.Member = null;
                binding.MemberId = null;
                return binding;
            }
            binding.Result = Concrete(resultType);
        }
        return binding;
    }

    // The index among the site's dynamic operands of the call's argument i, or -1.
    private static int DynamicArgument(Site site, int i)
    {
        if (site.Op is not IDynamicInvocationOperation inv || i >= inv.Arguments.Length)
        {
            return -1;
        }
        return site.Operands.IndexOf(inv.Arguments[i]);
    }

    private static string BinderMessage(Site site, List<string> types, SymbolInfo info)
    {
        string why = info.CandidateReason != CandidateReason.None ? " (" + Words(info.CandidateReason.ToString()) + ")" : "";
        string Of(IOperation? operand, ITypeSymbol? fallback)
        {
            int i = operand == null ? -1 : site.Operands.IndexOf(operand);
            return i >= 0 ? TypeOf[types[i]].ToDisplayString() : fallback?.ToDisplayString() ?? "";
        }
        switch (site.Op)
        {
            case IDynamicMemberReferenceOperation m:
                return "'" + Of(m.Instance, m.ContainingType) + "' does not contain a definition for '" + m.MemberName + "'";
            case IDynamicInvocationOperation { Operation: IDynamicMemberReferenceOperation m }:
                return info.CandidateSymbols.Length == 0
                    ? "'" + Of(m.Instance, m.ContainingType) + "' does not contain a definition for '" + m.MemberName + "'"
                    : "The best overloaded method match for '" + m.MemberName + "' has some invalid arguments" + why;
            case IBinaryOperation bin:
                return "Operator '" + Operator(bin.OperatorKind) + "' cannot be applied to operands of type '"
                    + Of(bin.LeftOperand, bin.LeftOperand.Type) + "' and '" + Of(bin.RightOperand, bin.RightOperand.Type) + "'";
            default:
                return "the binder finds nothing for " + string.Join(", ", types.Select(t => Display(TypeOf[t]))) + why;
        }
    }

    // ------------------------------------------------------------------ output

    private static void WriteValues()
    {
        foreach (var entry in Nodes.OrderBy(n => n.Key, StringComparer.Ordinal))
        {
            var node = entry.Value;
            if (node.File == null || node.Declaration == null || node.Model == null || !Indexer.Known.Contains(node.File))
            {
                continue;
            }
            if (node.Types.Unknown != null)
            {
                PortSource.Refuse(node.Model, node.File, node.Declaration, "dynamic", node.What + ": " + node.Types.Unknown);
                continue;
            }
            string? type = PortSource.TypeOf(node.Model, node.Declaration);
            if (type == null)
            {
                continue;
            }
            Indexer.Write(new Dictionary<string, object?>
            {
                ["t"] = "dynval",
                ["type"] = type,
                ["what"] = node.What,
                ["symbol"] = node.Symbol == null || node.Symbol is ILocalSymbol ? null : Indexer.Id(node.Symbol),
                ["public"] = node.Public,
                ["types"] = node.Types.Types.Select(t => Display(TypeOf[t])).ToList(),
                ["file"] = node.File,
                ["line"] = node.Declaration.GetLocation().GetLineSpan().StartLinePosition.Line + 1,
            });
        }
    }

    private static void WriteSites()
    {
        foreach (var site in Sites.OrderBy(s => s.File, StringComparer.Ordinal).ThenBy(s => s.Node.SpanStart))
        {
            if (!Indexer.Known.Contains(site.File))
            {
                continue;
            }
            string text = Shorten(site.Node.ToString());
            if (site.Unknown != null)
            {
                PortSource.Refuse(site.Model, site.File, site.Node, "dynamic", text + ": " + site.Unknown);
                continue;
            }
            var unseen = site.Bindings.Where(b => b.Unseen != null).Select(b => b.Unseen!).Distinct().ToList();
            if (unseen.Count > 0)
            {
                PortSource.Refuse(site.Model, site.File, site.Node, "dynamic", text + ": " + string.Join(", ", unseen) + " stays C# and reaches it");
                continue;
            }
            string? type = PortSource.TypeOf(site.Model, site.Node);
            if (type == null || site.Bindings.Count == 0
                || (site.ConvertTo != null && site.Bindings.All(b => b.Member != null && b.Member.StartsWith("an identity", StringComparison.Ordinal))))
            {
                continue; // nothing reaches it, or an identity conversion: nothing to tell the port
            }
            Indexer.Write(new Dictionary<string, object?>
            {
                ["t"] = "dyn",
                ["type"] = type,
                ["member"] = Indexer.Enclosing(site.Model, site.Node),
                ["kind"] = site.Kind,
                ["text"] = text,
                ["operands"] = site.Operands.Select(o => Shorten(o.Syntax.ToString())).ToList(),
                ["bindings"] = site.Bindings.Select(b => (object)new Dictionary<string, object?>
                {
                    ["types"] = b.Types.Select(t => Display(TypeOf[t])).ToList(),
                    ["member"] = b.Member,
                    ["member_id"] = b.MemberId,
                    ["result"] = b.Result != null ? b.Result.Types.Select(t => Display(TypeOf[t])).ToList()
                        : b.ResultNode != null && Nodes.TryGetValue(b.ResultNode, out var returned) ? returned.Types.Types.Select(t => Display(TypeOf[t])).ToList()
                        : new List<string>(),
                    ["error"] = b.Error,
                }).ToList(),
                ["file"] = site.File,
                ["line"] = site.Node.GetLocation().GetLineSpan().StartLinePosition.Line + 1,
            });
        }
    }

    // ------------------------------------------------------------------ helpers

    private static bool Dyn(ITypeSymbol? type) => type?.TypeKind == TypeKind.Dynamic;

    private static bool InSource(ISymbol symbol) => symbol.OriginalDefinition.Locations.Any(l => l.IsInSource);

    private static string Key(ITypeSymbol type) => type.ToDisplayString(SymbolDisplayFormat.FullyQualifiedFormat);

    private static string Display(ITypeSymbol? type) => type?.ToDisplayString(TypeName) ?? "?";

    // " (src/Fees/Fee.cs)": where a type of the solution is declared.
    private static string Where(ITypeSymbol type)
    {
        var location = type.Locations.FirstOrDefault(l => l.IsInSource);
        return location?.SourceTree == null ? "" : " (" + location.SourceTree.FilePath + ")";
    }

    // Whether the site's code can name the type: its own assembly's, or one it references.
    private static bool Visible(ITypeSymbol type, Compilation compilation)
    {
        if (type.SpecialType != SpecialType.None || type.ContainingAssembly == null)
        {
            return true;
        }
        if (type is IArrayTypeSymbol array)
        {
            return Visible(array.ElementType, compilation);
        }
        string assembly = type.ContainingAssembly.Name;
        if (type.IsAnonymousType)
        {
            return assembly == compilation.AssemblyName;
        }
        return assembly == compilation.AssemblyName || compilation.ReferencedAssemblyNames.Any(a => a.Name == assembly);
    }

    private static bool Implements(IMethodSymbol method) =>
        method.ContainingType != null && method.ContainingType.AllInterfaces.SelectMany(i => i.GetMembers())
            .Any(m => SymbolEqualityComparer.Default.Equals(method.ContainingType.FindImplementationForInterfaceMember(m), method));

    private static bool Exposed(ISymbol symbol)
    {
        for (ISymbol? s = symbol; s != null && s is not INamespaceSymbol; s = s.ContainingSymbol)
        {
            if (s.DeclaredAccessibility is not (Accessibility.Public or Accessibility.Protected or Accessibility.ProtectedOrInternal))
            {
                return false;
            }
        }
        return true;
    }

    private static IMethodSymbol? EnclosingMethod(SemanticModel model, IOperation op)
    {
        var symbol = model.GetEnclosingSymbol(op.Syntax.SpanStart);
        while (symbol is IMethodSymbol { MethodKind: MethodKind.AnonymousFunction or MethodKind.LocalFunction } inner)
        {
            if (inner.MethodKind == MethodKind.LocalFunction)
            {
                return inner;
            }
            return null; // a lambda's return isn't its method's
        }
        return symbol as IMethodSymbol;
    }

    private static string NodeKey(ISymbol symbol)
    {
        var location = symbol.Locations.FirstOrDefault();
        string where = symbol is ILocalSymbol && location != null
            ? "@" + location.GetLineSpan().Path + ":" + location.SourceSpan.Start
            : "";
        string owner = symbol is ILocalSymbol || symbol is IParameterSymbol
            ? (symbol.ContainingSymbol?.ToDisplayString(MemberName) ?? "") + "/"
            : "";
        string name = symbol is ILocalSymbol || symbol is IParameterSymbol ? symbol.Name : symbol.ToDisplayString(MemberName);
        return symbol.Kind.ToString().ToLowerInvariant() + ":" + owner + name + where;
    }

    private static string Describe(Conversion c) =>
        c.IsIdentity ? "an identity" : c.IsNumeric ? (c.IsImplicit ? "an implicit numeric" : "an explicit numeric")
        : c.IsBoxing ? "a boxing" : c.IsUnboxing ? "an unboxing" : c.IsReference ? "a reference"
        : c.IsNullable ? "a nullable" : c.IsEnumeration ? "an enumeration" : "a";

    private static string Operator(BinaryOperatorKind kind) => kind switch
    {
        BinaryOperatorKind.Add => "+", BinaryOperatorKind.Subtract => "-", BinaryOperatorKind.Multiply => "*",
        BinaryOperatorKind.Divide => "/", BinaryOperatorKind.Remainder => "%", BinaryOperatorKind.And => "&",
        BinaryOperatorKind.Or => "|", BinaryOperatorKind.ExclusiveOr => "^", BinaryOperatorKind.LeftShift => "<<",
        BinaryOperatorKind.RightShift => ">>", BinaryOperatorKind.Equals => "==", BinaryOperatorKind.NotEquals => "!=",
        BinaryOperatorKind.LessThan => "<", BinaryOperatorKind.LessThanOrEqual => "<=",
        BinaryOperatorKind.GreaterThan => ">", BinaryOperatorKind.GreaterThanOrEqual => ">=",
        BinaryOperatorKind.ConditionalAnd => "&&", BinaryOperatorKind.ConditionalOr => "||",
        _ => kind.ToString(),
    };

    // "ConditionalAccess" as "conditional access".
    private static string Words(string name)
    {
        var text = new StringBuilder();
        foreach (char c in name)
        {
            if (char.IsUpper(c) && text.Length > 0)
            {
                text.Append(' ');
            }
            text.Append(char.ToLowerInvariant(c));
        }
        return text.ToString();
    }

    private static string Shorten(string text)
    {
        string one = string.Join(" ", text.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries).Select(l => l.Trim()));
        return one.Length <= 120 ? one : one.Substring(0, 117) + "...";
    }
}
