// CodeCoreMorph's C# index helper (SPEC 7.2, 11.3, D-65). Compiled by the C# adapter with the
// .NET SDK's own csc, against the SDK's Roslyn (Microsoft.CodeAnalysis), which it loads from
// the SDK at run time; nothing is downloaded for it.
//
// Usage: CsIndexHelper <roslyn dir> <request file>. The request is key=value lines: root=,
// output=, refpack=, then for each project: project=<path>, and after it its source=, define=,
// assets= (obj/project.assets.json) and reference=<project path> lines. Paths of sources and
// projects are relative to root, with forward slashes. The output is JSON lines: sym (a
// declaration), ref (a reference to a project symbol), dep (a file using a type another file
// declares) and problem.

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
    private static readonly SymbolDisplayFormat Signature = new SymbolDisplayFormat(
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

    private static readonly SymbolDisplayFormat Qualified = new SymbolDisplayFormat(
        globalNamespaceStyle: SymbolDisplayGlobalNamespaceStyle.Omitted,
        typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameAndContainingTypesAndNamespaces,
        genericsOptions: SymbolDisplayGenericsOptions.None,
        memberOptions: SymbolDisplayMemberOptions.IncludeContainingType);

    private static StreamWriter output = StreamWriter.Null;
    private static string root = "";
    private static readonly HashSet<string> Known = new HashSet<string>(StringComparer.Ordinal);
    private static readonly HashSet<string> Emitted = new HashSet<string>(StringComparer.Ordinal);

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
            var framework = Directory.Exists(refpack)
                ? Directory.GetFiles(refpack, "*.dll").Select(f => (MetadataReference)MetadataReference.CreateFromFile(f)).ToList()
                : new List<MetadataReference>();
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
        references.AddRange(PackageAssemblies(project.Assets));
        foreach (string r in project.References)
        {
            if (built.TryGetValue(r, out var other))
            {
                references.Add(other.ToMetadataReference());
            }
        }
        string name = Path.GetFileNameWithoutExtension(project.Path);
        return CSharpCompilation.Create(
            name,
            trees,
            references,
            new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: true));
    }

    // The compile assets the restore recorded for the project's first framework.
    private static IEnumerable<MetadataReference> PackageAssemblies(string assets)
    {
        var found = new List<MetadataReference>();
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
                    if (!library.Value.TryGetProperty("compile", out var compile)
                        || !libraries.TryGetProperty(library.Name, out var info)
                        || !info.TryGetProperty("path", out var libPath))
                    {
                        continue;
                    }
                    foreach (var asset in compile.EnumerateObject())
                    {
                        if (asset.Name.EndsWith("/_._", StringComparison.Ordinal))
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

    private static bool IsDeclarationName(SimpleNameSyntax name) =>
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
            INamedTypeSymbol t when t.IsRecord => "record",
            INamedTypeSymbol t when t.TypeKind == TypeKind.Class => "class",
            INamedTypeSymbol t when t.TypeKind == TypeKind.Struct => "struct",
            INamedTypeSymbol t when t.TypeKind == TypeKind.Interface => "interface",
            INamedTypeSymbol t when t.TypeKind == TypeKind.Enum => "enum",
            INamedTypeSymbol t when t.TypeKind == TypeKind.Delegate => "delegate",
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
        if (symbol is INamedTypeSymbol named)
        {
            signature = TypePrefix(named) + signature;
            var bases = new List<string>();
            if (named.BaseType != null && named.TypeKind == TypeKind.Class
                && named.BaseType.SpecialType != SpecialType.System_Object)
            {
                bases.Add(named.BaseType.ToDisplayString(Signature));
            }
            bases.AddRange(named.Interfaces.Select(i => i.ToDisplayString(Signature)));
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

    private static string? Enclosing(SemanticModel model, SyntaxNode node)
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
    private static string? Id(ISymbol symbol)
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

    private static void Write(Dictionary<string, object?> row)
    {
        output.WriteLine(JsonSerializer.Serialize(row));
    }
}
