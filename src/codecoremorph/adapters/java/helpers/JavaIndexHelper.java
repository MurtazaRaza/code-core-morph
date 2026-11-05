// The Java adapter's index helper (SPEC 7.2, 11.2, D-63): Spoon reads the project's sources and
// this program writes the normalized index rows as JSON lines, one object per line.
//
// It runs in the sandbox on the toolchain's newest JDK, compiled from this source by the adapter.
// The request is a text file of key=value lines: root (the worktree), output (the JSON-lines
// file), compliance (the Java level to parse at) and one source= line per file.
//
// Rows ("t"): "sym" a declaration, "ref" a reference to a project declaration, "dep" a file that
// uses a type another file declares, "problem" an element it couldn't read. IDs look like
// java:org.example.Codec, java:org.example.Codec#hex(byte[]), java:org.example.Codec#<init>(int)
// and java:org.example.Codec#counts.

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import spoon.Launcher;
import spoon.reflect.CtModel;
import spoon.reflect.code.CtConstructorCall;
import spoon.reflect.code.CtExecutableReferenceExpression;
import spoon.reflect.code.CtFieldAccess;
import spoon.reflect.code.CtInvocation;
import spoon.reflect.cu.SourcePosition;
import spoon.reflect.cu.position.DeclarationSourcePosition;
import spoon.reflect.declaration.CtAnnotationType;
import spoon.reflect.declaration.CtConstructor;
import spoon.reflect.declaration.CtElement;
import spoon.reflect.declaration.CtEnum;
import spoon.reflect.declaration.CtEnumValue;
import spoon.reflect.declaration.CtExecutable;
import spoon.reflect.declaration.CtField;
import spoon.reflect.declaration.CtInterface;
import spoon.reflect.declaration.CtMethod;
import spoon.reflect.declaration.CtModifiable;
import spoon.reflect.declaration.CtParameter;
import spoon.reflect.declaration.CtRecord;
import spoon.reflect.declaration.CtShadowable;
import spoon.reflect.declaration.CtType;
import spoon.reflect.declaration.CtTypeMember;
import spoon.reflect.declaration.ModifierKind;
import spoon.reflect.reference.CtExecutableReference;
import spoon.reflect.reference.CtFieldReference;
import spoon.reflect.reference.CtTypeReference;
import spoon.reflect.visitor.filter.TypeFilter;

public final class JavaIndexHelper {
    private Path root;
    private BufferedWriter out;
    private final Set<String> deps = new HashSet<>();
    private int problems;

    public static void main(String[] args) throws IOException {
        new JavaIndexHelper().run(Paths.get(args[0]));
    }

    private void run(Path requestFile) throws IOException {
        String output = null;
        int compliance = 8;
        List<String> sources = new ArrayList<>();
        for (String line : Files.readAllLines(requestFile, StandardCharsets.UTF_8)) {
            int eq = line.indexOf('=');
            if (eq < 0) {
                continue;
            }
            String key = line.substring(0, eq);
            String value = line.substring(eq + 1);
            switch (key) {
                case "root" -> root = Paths.get(value).toAbsolutePath().normalize();
                case "output" -> output = value;
                case "compliance" -> compliance = Integer.parseInt(value.trim());
                case "source" -> sources.add(value);
                default -> { }
            }
        }
        if (root == null || output == null) {
            throw new IllegalArgumentException("the request needs root= and output=");
        }
        Launcher launcher = new Launcher();
        launcher.getEnvironment().setNoClasspath(true);
        launcher.getEnvironment().setComplianceLevel(compliance);
        launcher.getEnvironment().setCommentEnabled(false);
        launcher.getEnvironment().setIgnoreDuplicateDeclarations(true);
        launcher.getEnvironment().setShouldCompile(false);
        for (String source : sources) {
            launcher.addInputResource(source);
        }
        try (BufferedWriter writer = Files.newBufferedWriter(Paths.get(output), StandardCharsets.UTF_8)) {
            out = writer;
            CtModel model;
            try {
                model = launcher.buildModel();
            } catch (RuntimeException e) {
                problem("Spoon couldn't build the model: " + e);
                return;
            }
            for (CtType<?> type : model.getAllTypes()) {
                declare(type);
                references(type);
            }
            for (String dep : deps) {
                out.write(dep);
                out.newLine();
            }
        }
    }

    // ------------------------------------------------------------------ declarations

    private void declare(CtType<?> type) throws IOException {
        if (type.isAnonymous() || type.isLocalType()) {
            return;
        }
        try {
            symbol(typeId(type), type.getSimpleName(), dotted(type.getQualifiedName()), typeKind(type),
                    type, typeSignature(type), type, true);
            for (CtTypeMember member : type.getTypeMembers()) {
                if (member instanceof CtType<?> nested) {
                    declare(nested);
                } else if (member instanceof CtMethod<?> method) {
                    boolean body = method.getBody() != null;
                    symbol(methodId(type, method.getSignature()), method.getSimpleName(),
                            dotted(type.getQualifiedName()) + "." + method.getSimpleName(), "method",
                            method, methodSignature(method), method, body);
                } else if (member instanceof CtConstructor<?> ctor) {
                    if (ctor.isImplicit()) {
                        continue;
                    }
                    symbol(constructorId(type, ctor), type.getSimpleName(),
                            dotted(type.getQualifiedName()) + "." + type.getSimpleName(), "constructor",
                            ctor, constructorSignature(type, ctor), ctor, true);
                } else if (member instanceof CtField<?> field) {
                    if (field.isImplicit()) {
                        continue;
                    }
                    String kind = field instanceof CtEnumValue<?> ? "enum_constant" : "field";
                    symbol(fieldId(type, field.getSimpleName()), field.getSimpleName(),
                            dotted(type.getQualifiedName()) + "." + field.getSimpleName(), kind,
                            field, fieldSignature(field), field, true);
                }
            }
        } catch (RuntimeException e) {
            problem("couldn't index " + type.getQualifiedName() + ": " + e);
        }
    }

    private void symbol(String id, String name, String qualified, String kind, CtElement element,
            String signature, CtModifiable modifiers, boolean definition) throws IOException {
        SourcePosition pos = element.getPosition();
        String file = file(pos);
        if (file == null) {
            return;
        }
        int[] start = start(pos);
        String boundary = modifiers.hasModifier(ModifierKind.NATIVE) ? "jni" : "none";
        out.write("{\"t\":\"sym\",\"id\":" + json(id) + ",\"name\":" + json(name) + ",\"qname\":"
                + json(qualified) + ",\"kind\":\"" + kind + "\",\"file\":" + json(file) + ",\"sl\":"
                + start[0] + ",\"sc\":" + start[1] + ",\"el\":" + Math.max(pos.getEndLine(), start[0])
                + ",\"ec\":" + Math.max(pos.getEndColumn(), 1) + ",\"sig\":" + json(signature)
                + ",\"vis\":\"" + visibility(modifiers) + "\",\"def\":" + (definition ? 1 : 0)
                + ",\"b\":\"" + boundary + "\"}");
        out.newLine();
    }

    private static String typeKind(CtType<?> type) {
        if (type instanceof CtAnnotationType<?>) {
            return "annotation";
        }
        if (type instanceof CtInterface<?>) {
            return "interface";
        }
        if (type instanceof CtEnum<?>) {
            return "enum";
        }
        if (type instanceof CtRecord) {
            return "record";
        }
        return "class";
    }

    private static String visibility(CtModifiable element) {
        if (element.hasModifier(ModifierKind.PUBLIC)) {
            return "public";
        }
        if (element.hasModifier(ModifierKind.PROTECTED)) {
            return "protected";
        }
        // Private and package-private: neither is an interface code outside the package uses.
        if (element instanceof CtTypeMember member && member.getDeclaringType() instanceof CtInterface<?>) {
            return "public"; // interface members are public unless they say otherwise
        }
        return "private";
    }

    // ------------------------------------------------------------------ references

    private void references(CtType<?> top) throws IOException {
        String fromFile = file(top.getPosition());
        if (fromFile == null) {
            return;
        }
        for (CtElement element : top.getElements(new TypeFilter<>(CtElement.class))) {
            try {
                reference(fromFile, element);
            } catch (RuntimeException e) {
                if (problems < 50) {
                    problem("couldn't read a reference in " + fromFile + ": " + e);
                }
            }
        }
    }

    private void reference(String fromFile, CtElement element) throws IOException {
        if (element instanceof CtInvocation<?> call) {
            executable(fromFile, element, call.getExecutable(), "call");
        } else if (element instanceof CtConstructorCall<?> call) {
            executable(fromFile, element, call.getExecutable(), "call");
        } else if (element instanceof CtExecutableReferenceExpression<?, ?> expression) {
            executable(fromFile, element, expression.getExecutable(), "call");
        } else if (element instanceof CtFieldAccess<?> access) {
            CtFieldReference<?> ref = access.getVariable();
            CtField<?> field = ref == null ? null : ref.getFieldDeclaration();
            if (field != null && inModel(field) && field.getDeclaringType() != null) {
                emit(fromFile, element, fieldId(field.getDeclaringType(), field.getSimpleName()), "use",
                        field);
            }
        } else if (element instanceof CtTypeReference<?> ref) {
            if (ref.isImplicit()) {
                return;
            }
            CtType<?> type = ref.getTypeDeclaration();
            if (type != null && inModel(type) && !type.isAnonymous()) {
                CtElement parent = ref.getParent();
                boolean inherit = parent instanceof CtType<?> owner
                        && (ref.equals(owner.getSuperclass()) || owner.getSuperInterfaces().contains(ref));
                emit(fromFile, ref, typeId(type), inherit ? "inherit" : "type_use", type);
            }
        } else if (element instanceof CtMethod<?> method) {
            Collection<CtMethod<?>> tops = method.getTopDefinitions();
            for (CtMethod<?> overridden : tops) {
                if (overridden != method && inModel(overridden) && overridden.getDeclaringType() != null) {
                    emit(fromFile, method,
                            methodId(overridden.getDeclaringType(), overridden.getSignature()), "override",
                            overridden);
                }
            }
        }
    }

    private void executable(String fromFile, CtElement at, CtExecutableReference<?> ref, String kind)
            throws IOException {
        if (ref == null) {
            return;
        }
        CtExecutable<?> target = ref.getExecutableDeclaration();
        if (target == null || !inModel(target)) {
            return;
        }
        if (target instanceof CtMethod<?> method && method.getDeclaringType() != null) {
            emit(fromFile, at, methodId(method.getDeclaringType(), method.getSignature()), kind, target);
        } else if (target instanceof CtConstructor<?> ctor && ctor.getDeclaringType() != null) {
            if (ctor.isImplicit()) {
                emit(fromFile, at, typeId(ctor.getDeclaringType()), "type_use", ctor.getDeclaringType());
            } else {
                emit(fromFile, at, constructorId(ctor.getDeclaringType(), ctor), kind, target);
            }
        }
    }

    private void emit(String fromFile, CtElement at, String to, String kind, CtElement target)
            throws IOException {
        SourcePosition pos = at.getPosition();
        if (pos == null || !pos.isValidPosition()) {
            return;
        }
        String toFile = file(target.getPosition());
        if (toFile != null && !toFile.equals(fromFile)) {
            deps.add("{\"t\":\"dep\",\"from\":" + json(fromFile) + ",\"to\":" + json(toFile) + "}");
        }
        String from = enclosing(at);
        out.write("{\"t\":\"ref\",\"file\":" + json(fromFile) + ",\"from\":" + (from == null ? "null" : json(from))
                + ",\"to\":" + json(to) + ",\"kind\":\"" + kind + "\",\"line\":" + pos.getLine()
                + ",\"col\":" + Math.max(pos.getColumn(), 1) + "}");
        out.newLine();
    }

    private String enclosing(CtElement element) {
        CtElement current = element.getParent();
        while (current != null) {
            if (current instanceof CtMethod<?> method && method.getDeclaringType() != null
                    && !method.getDeclaringType().isAnonymous()) {
                return methodId(method.getDeclaringType(), method.getSignature());
            }
            if (current instanceof CtConstructor<?> ctor && ctor.getDeclaringType() != null
                    && !ctor.getDeclaringType().isAnonymous()) {
                return constructorId(ctor.getDeclaringType(), ctor);
            }
            if (current instanceof CtType<?> type && !type.isAnonymous() && !type.isLocalType()) {
                return typeId(type);
            }
            if (!current.isParentInitialized()) {
                return null;
            }
            current = current.getParent();
        }
        return null;
    }

    private boolean inModel(CtElement element) {
        if (element instanceof CtShadowable shadowable && shadowable.isShadow()) {
            return false;
        }
        return file(element.getPosition()) != null;
    }

    // ------------------------------------------------------------------ names and text

    private static String typeId(CtType<?> type) {
        return "java:" + type.getQualifiedName();
    }

    private static String methodId(CtType<?> type, String signature) {
        return "java:" + type.getQualifiedName() + "#" + signature;
    }

    private static String constructorId(CtType<?> type, CtConstructor<?> ctor) {
        String signature = ctor.getSignature();
        int paren = signature.indexOf('(');
        return "java:" + type.getQualifiedName() + "#<init>" + (paren < 0 ? "()" : signature.substring(paren));
    }

    private static String fieldId(CtType<?> type, String name) {
        return "java:" + type.getQualifiedName() + "#" + name;
    }

    private static String dotted(String qualified) {
        return qualified.replace('$', '.');
    }

    private static String modifiers(CtModifiable element) {
        StringBuilder text = new StringBuilder();
        for (ModifierKind kind : new LinkedHashSet<>(element.getModifiers())) {
            text.append(kind.toString()).append(' ');
        }
        return text.toString();
    }

    private static String typeSignature(CtType<?> type) {
        StringBuilder text = new StringBuilder(modifiers(type));
        text.append(typeKind(type).equals("annotation") ? "@interface" : typeKind(type)).append(' ');
        text.append(type.getSimpleName());
        if (!type.getFormalCtTypeParameters().isEmpty()) {
            List<String> params = new ArrayList<>();
            type.getFormalCtTypeParameters().forEach(p -> params.add(p.toString()));
            text.append('<').append(String.join(", ", params)).append('>');
        }
        if (type.getSuperclass() != null) {
            text.append(" extends ").append(type.getSuperclass());
        }
        if (!type.getSuperInterfaces().isEmpty()) {
            List<String> names = new ArrayList<>();
            type.getSuperInterfaces().forEach(i -> names.add(i.toString()));
            text.append(type instanceof CtInterface<?> ? " extends " : " implements ")
                    .append(String.join(", ", names));
        }
        return text.toString();
    }

    private static String parameters(List<CtParameter<?>> params) {
        List<String> types = new ArrayList<>();
        for (CtParameter<?> param : params) {
            types.add(String.valueOf(param.getType()) + (param.isVarArgs() ? "..." : ""));
        }
        return String.join(", ", types);
    }

    private static String thrown(CtExecutable<?> executable) {
        if (executable.getThrownTypes().isEmpty()) {
            return "";
        }
        List<String> names = new ArrayList<>();
        executable.getThrownTypes().forEach(t -> names.add(t.toString()));
        return " throws " + String.join(", ", names);
    }

    private static String methodSignature(CtMethod<?> method) {
        StringBuilder text = new StringBuilder(modifiers(method));
        if (!method.getFormalCtTypeParameters().isEmpty()) {
            List<String> params = new ArrayList<>();
            method.getFormalCtTypeParameters().forEach(p -> params.add(p.toString()));
            text.append('<').append(String.join(", ", params)).append("> ");
        }
        text.append(method.getType()).append(' ').append(method.getSimpleName());
        text.append('(').append(parameters(method.getParameters())).append(')').append(thrown(method));
        return text.toString();
    }

    private static String constructorSignature(CtType<?> type, CtConstructor<?> ctor) {
        return modifiers(ctor) + type.getSimpleName() + "(" + parameters(ctor.getParameters()) + ")"
                + thrown(ctor);
    }

    private static String fieldSignature(CtField<?> field) {
        return modifiers(field) + field.getType() + " " + field.getSimpleName();
    }

    // ------------------------------------------------------------------ positions

    private String file(SourcePosition pos) {
        if (pos == null || !pos.isValidPosition() || pos.getFile() == null) {
            return null;
        }
        Path path = pos.getFile().toPath().toAbsolutePath().normalize();
        if (!path.startsWith(root)) {
            return null;
        }
        return root.relativize(path).toString().replace('\\', '/');
    }

    private static int[] start(SourcePosition pos) {
        int offset = pos.getSourceStart();
        if (pos instanceof DeclarationSourcePosition declaration) {
            offset = Math.min(offset, declaration.getModifierSourceStart());
        }
        int[] separators = pos.getCompilationUnit() == null ? null
                : pos.getCompilationUnit().getLineSeparatorPositions();
        if (separators == null || offset < 0 || offset > pos.getSourceStart()) {
            return new int[] {pos.getLine(), Math.max(pos.getColumn(), 1)};
        }
        int line = 1;
        int lineStart = 0;
        for (int separator : separators) {
            if (separator >= offset) {
                break;
            }
            line++;
            lineStart = separator + 1;
        }
        return new int[] {line, offset - lineStart + 1};
    }

    private void problem(String message) throws IOException {
        problems++;
        out.write("{\"t\":\"problem\",\"message\":" + json(message) + "}");
        out.newLine();
    }

    private static String json(String text) {
        if (text == null) {
            return "null";
        }
        StringBuilder out = new StringBuilder(text.length() + 2).append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        return out.append('"').toString();
    }
}
